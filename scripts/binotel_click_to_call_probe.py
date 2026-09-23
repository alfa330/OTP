# -*- coding: utf-8 -*-
"""Живая проверка click-to-call Binotel для обзвона из iCORE Phone.

Что делает: просит АТС соединить внутреннюю линию оператора с внешним номером
(calls/internal-number-to-external-number), печатает generalCallID и затем
опрашивает stats/call-details, пока звонок не завершится (или не выйдет время).

Зачем: до того как писать обзвон в телефоне, нужно знать четыре вещи и все
четыре видны только живым звонком:
  1) допущен ли ключ API компании к группе calls (ошибка 104 = «дозвон по API не
     включён на линии», включает поддержка Binotel);
  2) какие disposition приходят при ответе / недозвоне / занято / сбросе;
  3) как быстро call-details «видит» звонок и сколько идёт от запроса до гудка;
  4) принимает ли метод дополнительные параметры (callerIdForEmployee,
     playbackWaiting, callTimeToExt) — в официальных примерах их нет.

Что видит внутренняя линия во From — этот скрипт не покажет: смотреть в
диагностическом журнале iCORE Phone (строки [sip] INVITE) на самом телефоне.

Примеры:
    python scripts/binotel_click_to_call_probe.py --company remote_cc --internal 904 --external 77011234567
    python scripts/binotel_click_to_call_probe.py --company remote_cc --internal 904 --external 77011234567 \
        --extra callerIdForEmployee=100 --extra playbackWaiting=false
    python scripts/binotel_click_to_call_probe.py --company remote_cc --details 123456789
    python scripts/binotel_click_to_call_probe.py --company remote_cc --hangup 123456789

Ключ и секрет — переменные <PREFIX>_API_KEY / <PREFIX>_API_SECRET (для remote_cc —
REMOTE_CC_BINOTEL_API_KEY / REMOTE_CC_BINOTEL_API_SECRET) в окружении или в
.env.codex.local. В вывод они не попадают.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binotel import client as binotel  # noqa: E402

POLL_INTERVAL_SEC = 3
FINAL_DISPOSITIONS_HINT = "ANSWER/ANSWERED — ответил; BUSY — занято; NOANSWER/CANCEL — не дозвонились"


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _parse_extra(items):
    """['k=v', ...] -> {k: v}; true/false/числа приводим к типам, остальное строкой."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--extra ждёт вид ключ=значение, получено: {item!r}")
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        low = value.lower()
        if low in ("true", "false"):
            out[key] = (low == "true")
        elif value.isdigit():
            out[key] = int(value)
        else:
            out[key] = value
    return out


def _print_call(call):
    keys = ("general_call_id", "disposition", "call_type", "internal_number", "external_number",
            "billsec", "waitsec", "start_time", "employee_name", "call_end_party", "recording_status")
    print("   ", json.dumps({k: call.get(k) for k in keys}, ensure_ascii=False))


def watch(client, general_call_id, wait_sec):
    """Опрашивать call-details до финальной disposition или до wait_sec."""
    deadline = time.monotonic() + wait_sec
    last = None
    first_seen_at = None
    while time.monotonic() < deadline:
        try:
            details = client.call_details(general_call_id)
        except Exception as exc:  # лимит частоты / сеть: не валим наблюдение
            print(f"{_now()}  call-details: ошибка {exc}")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        call = details.get(str(general_call_id))
        if not call:
            state = "ещё не виден в статистике"
        else:
            if first_seen_at is None:
                first_seen_at = time.monotonic()
                print(f"{_now()}  звонок появился в call-details")
            state = call.get("disposition") or "(disposition пуст — идёт набор)"
        if state != last:
            print(f"{_now()}  состояние: {state}")
            if call:
                _print_call(call)
            last = state
        if call and call.get("disposition") and call["disposition"] != "ONLINE":
            print(f"{_now()}  звонок завершён. Подсказка: {FINAL_DISPOSITIONS_HINT}")
            return call
        time.sleep(POLL_INTERVAL_SEC)
    print(f"{_now()}  время наблюдения вышло ({wait_sec} с); последнее состояние: {last}")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--company", default=binotel.COMPANY_REMOTE_CC,
                        help="имя компании Binotel (remote_cc, …) либо префикс переменных окружения")
    parser.add_argument("--internal", help="внутренний номер сотрудника (линия, на которую позвонит АТС)")
    parser.add_argument("--external", help="внешний номер, куда звоним после ответа сотрудника")
    parser.add_argument("--extra", action="append", default=[],
                        help="дополнительный параметр запроса, ключ=значение (можно несколько)")
    parser.add_argument("--wait", type=int, default=120, help="сколько секунд наблюдать за звонком")
    parser.add_argument("--details", help="только показать call-details по generalCallID")
    parser.add_argument("--hangup", help="только завершить звонок по generalCallID")
    parser.add_argument("--no-watch", action="store_true", help="не опрашивать call-details после запуска")
    args = parser.parse_args()

    cfg = binotel.get_config(company=args.company)
    if not binotel.api_ready(cfg):
        raise SystemExit(f"Ключ Binotel API не задан: {cfg['env_prefix']}_API_KEY / {cfg['env_prefix']}_API_SECRET")
    client = binotel.BinotelApiClient.from_config(cfg)
    print(f"компания={cfg['company']}  api={cfg['base_url']}  tz={cfg['tz']}")

    if args.details:
        details = client.call_details(args.details)
        if not details:
            print("call-details: звонок не найден (ещё не начался или id чужой)")
        for call in details.values():
            _print_call(call)
        return
    if args.hangup:
        print("hangup-call:", json.dumps(client.hangup_call(args.hangup), ensure_ascii=False)[:300])
        return

    if not (args.internal and args.external):
        raise SystemExit("Нужны --internal и --external (или --details / --hangup)")
    extra = _parse_extra(args.extra)
    print(f"{_now()}  запрос: internalNumber={args.internal} externalNumber={args.external}"
          + (f" extra={json.dumps(extra, ensure_ascii=False)}" if extra else ""))
    started = time.monotonic()
    try:
        general_call_id = client.originate_internal_to_external(args.internal, args.external, **extra)
    except Exception as exc:
        print(f"{_now()}  ОТКАЗ: {exc}")
        print("   если в тексте code=104 — попросить поддержку Binotel включить «дозвон по API» для линии")
        raise SystemExit(2)
    print(f"{_now()}  принято за {time.monotonic() - started:.1f} с, generalCallID={general_call_id}")
    print("   сейчас АТС должна звонить на внутреннюю линию; смотрите телефон оператора")
    if not args.no_watch:
        watch(client, general_call_id, args.wait)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
