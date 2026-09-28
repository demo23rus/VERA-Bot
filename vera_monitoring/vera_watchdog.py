#!/usr/bin/env python3
import json
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
ENV_FILE = Path("/root/.env_vera")
STATE_FILE = Path("/root/.vera_monitoring_state.json")
REMINDER_SECONDS = 6 * 60 * 60
HEARTBEAT_MAX_AGE = 150
STARTUP_GRACE_SECONDS = 75
HEALTH_RETRIES = 3
HEALTH_RETRY_DELAY_SECONDS = 5
RESTART_SETTLE_SECONDS = 20

BOTS = {
    "telegram": {
        "title": "С верой Telegram",
        "service": "vera-bot.service",
        "heartbeat": Path("/tmp/vera_telegram.heartbeat"),
        "health_url": None,
    },
    "max": {
        "title": "С верой MAX",
        "service": "vera-max.service",
        "heartbeat": Path("/tmp/vera_max.heartbeat"),
        "health_url": "http://127.0.0.1:8080/health",
    },
}


def load_env():
    data = {}
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                key, value = raw.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def notify(text):
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_OWNER_ID") or "").strip()
    if not token or not chat_id:
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except Exception:
        return False


def is_active(service):
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        check=False,
    ).returncode == 0


def service_uptime_seconds(service):
    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveEnterTimestampMonotonic", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        entered_us = int((result.stdout or "0").strip() or 0)
        if entered_us <= 0:
            return None
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            uptime_seconds = float(fh.read().split()[0])
        return max(0.0, uptime_seconds - entered_us / 1_000_000)
    except Exception:
        return None


def heartbeat_status(path):
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
        return age <= HEARTBEAT_MAX_AGE, round(age, 1)
    except OSError:
        return False, None


def health_status(url):
    if not url:
        return True
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.status == 200
    except Exception:
        return False


def health_status_stable(url):
    if not url:
        return True
    for attempt in range(HEALTH_RETRIES):
        if health_status(url):
            return True
        if attempt < HEALTH_RETRIES - 1:
            time.sleep(HEALTH_RETRY_DELAY_SECONDS)
    return False


def restart(service):
    subprocess.run(["systemctl", "restart", service], check=False, timeout=30)
    time.sleep(RESTART_SETTLE_SECONDS)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def inspect(cfg):
    service_ok = is_active(cfg["service"])
    uptime = service_uptime_seconds(cfg["service"]) if service_ok else None
    heartbeat_ok, age = heartbeat_status(cfg["heartbeat"])

    # Во время нормального старта не считаем ещё не поднятый /health аварией.
    in_startup_grace = service_ok and uptime is not None and uptime < STARTUP_GRACE_SECONDS
    endpoint_ok = True if in_startup_grace else health_status_stable(cfg["health_url"])

    reasons = []
    if not service_ok:
        reasons.append("systemd-сервис неактивен")
    if not heartbeat_ok and not in_startup_grace:
        reasons.append("heartbeat отсутствует или устарел")
    if not endpoint_ok:
        reasons.append("/health не отвечает после повторных проверок")

    heartbeat_effective = heartbeat_ok or in_startup_grace
    return service_ok and heartbeat_effective and endpoint_ok, reasons, age


def main():
    state = load_state()
    now = time.time()

    for key, cfg in BOTS.items():
        previous = state.get(key, {})
        ok, reasons, age = inspect(cfg)

        if not ok:
            restart(cfg["service"])
            ok, reasons, age = inspect(cfg)

        previous_ok = previous.get("ok")
        last_alert = float(previous.get("last_alert", 0) or 0)

        if ok and previous_ok is False:
            notify(
                f"✅ {cfg['title']} восстановлен\n\n"
                f"Сервис: {cfg['service']}\n"
                f"Heartbeat: {age} сек."
            )
            last_alert = now
        elif not ok and (
            previous_ok is not False or now - last_alert >= REMINDER_SECONDS
        ):
            notify(
                f"🚨 {cfg['title']} не восстановлен\n\n"
                f"Сервис: {cfg['service']}\n"
                f"Причина: {', '.join(reasons) or 'неизвестна'}\n"
                "Watchdog выполнил автоматический перезапуск."
            )
            last_alert = now

        state[key] = {
            "ok": ok,
            "last_check": now,
            "last_alert": last_alert,
            "heartbeat_age": age,
            "reasons": reasons,
        }

    now_msk = datetime.now(MOSCOW)
    today = now_msk.strftime("%Y-%m-%d")
    if now_msk.hour >= 9 and state.get("daily_report_date") != today:
        lines = ["🛡 Ежедневный отчёт «С верой»", ""]
        for key, cfg in BOTS.items():
            icon = "✅" if state.get(key, {}).get("ok") else "❌"
            lines.append(f"{icon} {cfg['title']}")
        notify("\n".join(lines))
        state["daily_report_date"] = today

    save_state(state)


if __name__ == "__main__":
    main()
