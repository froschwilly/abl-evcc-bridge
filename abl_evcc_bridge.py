#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 frosch.willy
"""
Bridge zwischen ABL-Sursum EVCC-Pilotmodul (Dok.-Nr. 03 0009 01 D) und
evcc (charger type: custom, source: script) - über einen transparenten
TCP/IP<->RS485-Adapter, der exklusiv an der Wallbox hängt.

WICHTIGE HINWEISE
- Nur einphasig (6-32 A) getestet; keine Phasenumschaltung.
- Workaround ohne Gewähr und ohne Support, Nutzung auf eigene Verantwortung
  (230-V-Ladeanwendung).
- Skript und Dokumentation wurden gemeinsam mit dem KI-Assistenten Claude
  (Anthropic, claude.ai) erarbeitet und ausschließlich am realen Aufbau des
  Autors getestet. Bitte den Code vor dem Einsatz selbst prüfen.
- Am RS485-Bus darf nur EIN Master hängen. Ein evtl. vorhandener
  Datenlogger/eine andere Steuerung der Box muss vom Bus getrennt sein.
- Empfohlener evcc-Modus: "Smart" mit "Dauerhaft laden (min. 6 A)" - siehe
  Dokumentation.

evcc ruft dieses Skript für jede Aktion separat auf und liest den Wert
von stdout:

    abl_evcc_bridge.py status               -> A / B / C / F
    abl_evcc_bridge.py enabled               -> true / false
    abl_evcc_bridge.py enable true|false     -> Ladefreigabe setzen
    abl_evcc_bridge.py maxcurrent <Ampere>   -> Ladestrom setzen
    abl_evcc_bridge.py current               -> aktuell eingestellten
                                                  Sollstrom auslesen (A)

Konfiguration unten anpassen (IP/Port des Adapters, Modul-Adresse).

Sperre/Freigabe: enable(false) setzt das bBreakCharge-Flag (!n 27) und PWM
0999, enable(true) setzt den Mindeststrom und löscht das Flag (!n 28);
'enabled' liest das Flag (!n 29). Das Flag verhindert den Ladestart eines
angesteckten Fahrzeugs (Zustand B1), stoppt aber keine laufende Ladung -
dafür dient PWM 0999.

Statusmeldung an evcc (cmd_status): Zustand 0012 (Fahrzeug erkannt, Flag
gesetzt) wird als B gemeldet; bleibt 0012 nach dem Abstecken stehen, zeigt
die CP-Spannung (Funktion 07, ca. 12 V statt 9 V) das Abstecken -> A. Meldet
das Modul C, während Flag gesetzt und PWM 0999 ist (hängendes C nach dem
Sperren), wird B gemeldet.

MIT License

Copyright (c) 2026 frosch.willy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import fcntl
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

# ---- Konfiguration -----------------------------------------------------
# Transparenter TCP/IP<->RS485-Adapter im TCP-Server-Modus (Modbus-Gateway
# AUS!, 38400 8N1, kein Flow Control). Er reicht Bytes 1:1 durch, d.h. wir
# schicken den rohen ABL-Befehl inkl. <CR><LF>.
ADAPTER_HOST = "192.168.1.91"   # IP des TCP/IP-RS485-Adapters (anpassen)
ADAPTER_PORT = 552               # TCP-Port des Adapters (anpassen)

ADAPTER_TIMEOUT = 2.0            # Sekunden (Verbindungsaufbau)
READ_TIMEOUT = 1.0               # Sekunden (Warten auf die Antwortzeile)
RETRIES = 3                      # Versuche pro Befehl
RETRY_PAUSE = 0.3                # Sekunden zwischen zwei Versuchen
CMD_GAP = 0.1                    # Sekunden Pause nach jedem Befehl
# Sperrdatei: verhindert, dass mehrere Skriptaufrufe gleichzeitig auf den
# RS485-Bus zugreifen (Antworten wuerden sich sonst vermischen).
LOCK_FILE = "/tmp/abl_evcc_bus.lock"
# Abfragen, deren Antwort den Funktionscode enthalten muss.
READ_FUNCS = ("02", "07", "11", "29")

# Erkennung "Fahrzeug abgesteckt" bei gesetztem bBreakCharge-Flag: In diesem
# Fall bleibt der gemeldete Zustand 0012 nach dem Abstecken stehen (bis das
# Flag gelöscht wird), die CP-Spannung zeigt aber das Abstecken. Funktion 07
# liefert den ADC-Wert der positiven CP-Spannung, U[V] = 0,017 * Wert.
# Gemessen (E.ON Basisbox Kombi, Flag gesetzt, Zustand 0012):
#   Fahrzeug abgesteckt          : 0718 (ca. 12,2 V)
#   Fahrzeug angesteckt (gesperrt): 0530 (ca.  9,0 V)
#   direkt nach dem Abstecken    : 0716 (ca. 12,2 V), Zustand weiter 0012
# Ab diesem Wert (ca. 10,5 V) gilt "kein Fahrzeug".
CP_NO_CAR_RAW = 620

# "Haengendes C": Nach dem Sperren (Flag gesetzt, PWM 0999) fliesst praktisch
# kein Strom mehr, das Modul meldet aber noch minutenlang Zustand C. evcc
# sieht dann "disabled but charging" und pendelt zwischen enable true/false.
# Mit True meldet das Skript in diesem Fall B ("verbunden, laedt nicht").
REPORT_LOCKED_C_AS_B = True

# Debug-Log: protokolliert jeden enable/maxcurrent-Aufruf mit Zeitstempel,
# damit sich live nachvollziehen lässt, was evcc dem Skript tatsächlich
# schickt (z.B. per "tail -f /tmp/abl_evcc_debug.log"). Kann bei Bedarf
# einfach gelöscht/ignoriert werden. Zeitstempel in UTC (MEZ = UTC+1, MESZ = UTC+2).
DEBUG_LOG = "/tmp/abl_evcc_debug.log"

MODULE_ADDR = 1    # Adresse des EVCC-Moduls (1..8; Werkseinstellung 0 =
                    # Broadcast). Abfrage: "!0 23" -> z.B. ">1 23 0001"
                    # bedeutet Adresse 1.
# -------------------------------------------------------------------------

# Zustands-Codes lt. Tabelle "Abfrage Zustand" (Seite 6 der PDF)
STATE_MAP = {
    "0000": "A",   # Zustand A - kein Fahrzeug
    "0004": "B",   # Zustand B2 - Fahrzeug erkannt, wartet auf Ladefreigabe
    "0005": "C",   # Zustand C - lädt ohne Belüftung
    "0006": "C",   # Zustand D - lädt mit Belüftung
    "0009": "B",   # Zustand B' - Ladung durch EV beendet/unterbrochen
    "0013": "B",   # Zustand B1 - EV erkannt
    "0012": "B",   # im Datenblatt NICHT aufgeführt: Fahrzeug erkannt, aber
                    # bBreakCharge-Flag gesetzt (beobachtet: Flag=1 + Fahrzeug
                    # angesteckt -> 0012). Ohne diese Zeile meldet das Skript F,
                    # evcc kennt "F" nicht ("invalid status: F"), sieht das
                    # Fahrzeug nie und gibt die Box nie frei.
    "0017": "A",   # Zustand A' - wird von uns nicht mehr aktiv angesteuert
                    # (siehe Erklärung bei cmd_enable() zum Deadlock-Problem)
    "0033": "F",   # Fehler CS
    "0035": "F",   # Fehler EV (Kurzschluss PE/CP)
    "0037": "F",   # Fehler Verriegelung
    "0039": "F",   # Fehler Belüftung
    "0255": "B",   # Zustand MANUAL
}

# Direkter PWM-Bereich 0080..0970 (8,0%..97,0% in 0,1%-Schritten) statt
# der 9 festen Stromstufen - noetig fuer feine PV-Ueberschuss-Nachfuehrung.
#
# Umrechnung (IEC 61851-1, Bereich 8%-85% Duty-Cycle, linear):
#     I[A] = Duty[%] * 0,6      <=>      Duty[%] = I[A] / 0,6
#     PWM_raw (0,1%-Einheiten)  = Duty[%] * 10 = I[A] / 0,06
#
# ACHTUNG Erratum im Originalhandbuch (Seite 6/13): dort steht
# "ILADEN = PWM * 0.6A" - korrekt ist "PWM * 0,06A", da PWM (xxxx) in
# 0,1%-Schritten angegeben ist. Bestätigt durch Testabfrage:
# !0 11 -> >1 11 0100  entspricht 0100 * 0,06 = 6,0A (Minimalstrom).
MIN_CURRENT_A = 6     # IEC 61851 Mindeststrom
MAX_CURRENT_A = 32    # 1-phasig, 32A x 230V = 7,36kW (Hausanschluss-Limit)
FORBIDDEN_CODE = "0999"   # "Laden verboten" -> zum Sperren/Disable nutzen


def amps_to_pwm_code(amps):
    """Rechnet Ampere in den 4-stelligen PWM-Rohwert (0,1%-Schritte) um,
    begrenzt auf den 1-phasig zulässigen Bereich."""
    amps = max(MIN_CURRENT_A, min(MAX_CURRENT_A, float(amps)))
    pwm_raw = round(amps / 0.06)      # = amps * 100 / 6
    pwm_raw = max(80, min(970, pwm_raw))   # Sicherheitsclamp lt. Datenblatt
    return f"{pwm_raw:04d}"


def pwm_code_to_amps(pwm_raw_str):
    """Kehrfunktion zu amps_to_pwm_code(): PWM-Rohwert -> Ampere
    (korrigierte Formel, siehe Kommentar oben).

    WICHTIG: Der Sonderwert 0999 ("Laden verboten", siehe FORBIDDEN_CODE)
    ist KEIN echter Duty-Cycle-Wert im Bereich 0080..0970, sondern ein
    reiner Kommando-Code. Das Modul scheint diesen Wert bei einer
    "Abfrage aktuelle PWM" unverändert zurückzuspiegeln, statt ihn in
    einen echten (0%) Duty-Wert zu übersetzen. Ohne Sonderbehandlung
    würde die normale Formel daraus fälschlich "999 * 0.06 = 59.94A"
    berechnen - obwohl tatsächlich 0A fließen (Ladung ist ja gerade
    gesperrt). Gleiches gilt vorsorglich für jeden anderen Wert oberhalb
    des gültigen Direktbereichs (>970)."""
    pwm_raw = int(pwm_raw_str)
    if pwm_raw >= 971:
        return 0.0
    return round(pwm_raw * 0.06, 2)


def _acquire_bus_lock():
    """Exklusive Sperre ueber alle gleichzeitig laufenden Skriptaufrufe.
    Gibt den Dateideskriptor zurueck (oder None, falls die Sperre nicht
    moeglich ist - dann wird ohne Sperre weitergearbeitet)."""
    try:
        fd = os.open(LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o666)
    except OSError:
        return None
    try:
        os.chmod(LOCK_FILE, 0o666)   # fuer alle Benutzer (admin-Test / evcc-Dienst)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_bus_lock(fd):
    if fd is not None:
        os.close(fd)   # Schliessen gibt die flock-Sperre frei


def _exchange(cmd):
    """Ein Versuch: verbinden, alte Reste verwerfen, senden, bis zum
    Zeilenende (LF) lesen. Liefert den Rohtext (evtl. leer)."""
    payload = f"{cmd}\r\n".encode("ascii")
    with socket.create_connection((ADAPTER_HOST, ADAPTER_PORT),
                                  timeout=ADAPTER_TIMEOUT) as sock:
        # Reste im Empfangspuffer (z.B. verspaetete Antworten) verwerfen
        sock.settimeout(0.05)
        try:
            while sock.recv(256):
                pass
        except (socket.timeout, BlockingIOError):
            pass
        sock.sendall(payload)
        sock.settimeout(READ_TIMEOUT)
        data = b""
        try:
            while b"\n" not in data:
                chunk = sock.recv(256)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    return data.decode("ascii", errors="replace")


def _pick_reply(text, func):
    """Sucht in der Rohantwort die Zeile, die zum gesendeten Funktionscode
    passt (z.B. '>1 29 0001'); alles andere wird ignoriert."""
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        m = re.search(rf">\s*\d+\s+{func}\b.*", line)
        if m:
            return m.group(0)
    return None


def evccsend(cmd, func=None):
    """Sendet einen rohen ABL-EVCC-Befehl (z.B. '!1 11' oder '!1 12 0100')
    inkl. <CR><LF> per TCP an den transparenten RS485-Adapter, der die Bytes
    1:1 an das Modul durchreicht, und liefert die Rohantwortzeile zurueck.

    Gegenueber der ersten Version: Zugriff ueber eine Sperrdatei
    serialisiert, bei Abfragen (READ_FUNCS) muss die Antwort zum Befehl
    passen, bei fehlender/falscher Antwort wird mehrfach wiederholt."""
    expect = func in READ_FUNCS
    fd = _acquire_bus_lock()
    try:
        last_err = "?"
        for attempt in range(1, RETRIES + 1):
            try:
                text = _exchange(cmd)
                reply = _pick_reply(text, func) if expect else (text.strip() or None)
                if reply is not None:
                    time.sleep(CMD_GAP)
                    return reply
                last_err = f"keine passende Antwort ({text.strip()!r})"
            except OSError as exc:
                last_err = str(exc)
            _debug_log(f"Versuch {attempt}/{RETRIES} fuer '{cmd}' "
                       f"fehlgeschlagen: {last_err}")
            time.sleep(RETRY_PAUSE)
        # Details auf stderr (nicht stdout - das liest evcc als Wert!)
        print(f"[abl_evcc_bridge] '{cmd}' an {ADAPTER_HOST}:{ADAPTER_PORT} "
              f"ohne gueltige Antwort: {last_err}", file=sys.stderr)
        return ""
    finally:
        _release_bus_lock(fd)


def send_cmd(func, param=None):
    """Baut den ABL-Befehl (!<addr> <func> [<param>]) und schickt ihn
    per evccsend()."""
    if param is not None:
        cmd = f"!{MODULE_ADDR} {func} {param}"
    else:
        cmd = f"!{MODULE_ADDR} {func}"
    return evccsend(cmd, func)


VALUE_RE = re.compile(r"\d+")


def parse_response_value(resp):
    """Extrahiert den letzten rein numerischen Wert aus einer Modul-
    Antwort und ignoriert dabei Anhängsel wie ein angehängtes '_'
    (z.B. '>1 11 0100_' statt '>1 11 0100', so bei manchen Gateways
    beobachtet). Gibt None zurück, falls keine Ziffern gefunden wurden."""
    matches = VALUE_RE.findall(resp)
    return matches[-1] if matches else None


def query_state_code():
    resp = send_cmd("02")   # Abfrage Zustand
    value = parse_response_value(resp)
    return value.zfill(4) if value else None


def query_break_flag():
    """Fragt das bBreakCharge-Flag ab (Funktion 29, laut ABL-Datenblatt:
    "bBreakCharge=1 => Ladevorgang haelt in B1 an").

    Rückgabe: True (Flag gesetzt), False (Flag gelöscht) oder None
    (keine gültige Antwort, z.B. Verbindungsfehler oder ERR). Es werden
    nur die Werte 0000 und 0001 akzeptiert, damit z.B. eine Antwort wie
    '>1 29 ERR' (aus der nur der Funktionscode '29' extrahiert würde)
    nicht fälschlich als Wert gelesen wird."""
    resp = send_cmd("29")
    value = parse_response_value(resp)
    if value == "0001":
        return True
    if value == "0000":
        return False
    return None


def query_cp_high_raw():
    """Fragt den ADC-Wert der positiven CP-Spannung ab (Funktion 07).
    Rückgabe: Ganzzahl oder None bei ungültiger Antwort."""
    value = parse_response_value(send_cmd("07"))
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def cmd_status():
    code = query_state_code()
    status = STATE_MAP.get(code, "F")
    if code is not None and code not in STATE_MAP:
        _debug_log(f"unbekannter Zustandscode {code} -> Status F")
    if code == "0012":
        # Bei gesetztem Flag bleibt 0012 nach dem Abstecken stehen. Die
        # CP-Spannung zeigt das Abstecken trotzdem (ca. 12 V statt 9 V).
        # Bei ungültiger Antwort bleibt es bei B (wie bisher).
        cp = query_cp_high_raw()
        if cp is not None and cp >= CP_NO_CAR_RAW:
            _debug_log(f"Zustand 0012, CP-Spannung {cp} -> Fahrzeug abgesteckt, Status A")
            status = "A"
    if REPORT_LOCKED_C_AS_B and status == "C":
        # Haengendes C erkennen: Flag gesetzt UND PWM 0999 -> faktisch gesperrt.
        if query_break_flag() is True and query_pwm_raw() == int(FORBIDDEN_CODE):
            _debug_log("Zustand C, Flag gesetzt und PWM 0999 -> Status B")
            status = "B"
    print(status)


def _debug_log(msg):
    """Best-effort Log-Zeile, bricht nie mit Exception ab."""
    try:
        fd = os.open(DEBUG_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        with os.fdopen(fd, "a") as f:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def cmd_enabled():
    """Meldet die Freigabe direkt aus dem Modul: Flag gelöscht = 'true',
    Flag gesetzt = 'false'. Bei ungültiger Antwort Abbruch mit Fehler
    (evcc überspringt dann den Abruf), statt einen geratenen Wert zu
    melden."""
    flag = query_break_flag()
    if flag is None:
        print("[abl_evcc_bridge] enabled: keine gültige Antwort auf die "
              "Flag-Abfrage (!n 29)", file=sys.stderr)
        sys.exit(1)
    print("false" if flag else "true")


def query_pwm_raw():
    """Fragt den im Modul eingestellten PWM-Rohwert ab (Funktion 11).
    Rückgabe: Ganzzahl oder None bei ungültiger Antwort."""
    value = parse_response_value(send_cmd("11"))
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def cmd_enable(enable):
    """Freigabe/Sperre über das bBreakCharge-Flag plus PWM:

    - enable(false): ZUERST das Flag setzen (verhindert sofort jeden neuen
      Ladestart eines angesteckten Fahrzeugs, Zustand B1), DANN PWM 0999
      (beendet bzw. drosselt eine laufende Ladung - das Flag allein stoppt
      eine bereits laufende Ladung nicht).
    - enable(true): Ist im Modul bereits ein gültiger Zielstrom eingestellt
      (von cmd_maxcurrent() bei noch gesperrter Box vorgemerkt), wird er
      behalten. Sonst (z.B. 0999 nach dem Sperren) wird der Mindeststrom
      gesetzt. DANACH wird das Flag gelöscht, damit ein wartendes Fahrzeug
      nie mit einem ungültigen Stromwert startet.

    Hintergrund: evcc sendet beim Start einer Ladung zuerst maxcurrent und
    dann enable(true) und wiederholt maxcurrent nur, wenn sich der Zielwert
    ändert. Im Modus "Schnell" (konstant 32 A) ging der Zielwert deshalb
    verloren und die Ladung blieb bei 6 A hängen."""
    _debug_log(f"enable({enable}) aufgerufen")
    if enable:
        pwm = query_pwm_raw()
        lo = int(amps_to_pwm_code(MIN_CURRENT_A))
        hi = int(amps_to_pwm_code(MAX_CURRENT_A))
        if pwm is None or not (lo <= pwm <= hi):
            send_cmd("12", amps_to_pwm_code(MIN_CURRENT_A))
        else:
            _debug_log(f"enable(True): vorgemerkter Strom {pwm} bleibt erhalten")
        send_cmd("28")
    else:
        send_cmd("27")
        send_cmd("12", FORBIDDEN_CODE)


def cmd_maxcurrent(amps):
    """Setzt den Ladestrom.

    - Flag gelöscht: normal setzen.
    - Flag gesetzt (gesperrt): Meldet das Modul gerade Zustand C (laufende
      oder 'hängende' Ladung nach dem Sperren), wird NICHTS gesetzt - sonst
      würde die Ladung wieder anlaufen. In allen anderen Zuständen (A, B,
      0012) wird der Zielstrom vorgemerkt (das Flag verhindert den Start),
      damit cmd_enable(True) ihn übernimmt.
    - Flag-Abfrage ungültig: nichts tun."""
    flag = query_break_flag()
    _debug_log(f"maxcurrent({amps}) aufgerufen, Flag={flag}")
    if flag is None:
        return
    if flag and STATE_MAP.get(query_state_code()) == "C":
        _debug_log("gesperrt und Zustand C -> Strom nicht gesetzt")
        return
    send_cmd("12", amps_to_pwm_code(amps))


def cmd_current():
    """Liest den tatsächlich aktuell freigegebenen/fließenden Ladestrom
    aus (Funktion 11), umgerechnet mit der korrigierten Formel."""
    resp = send_cmd("11")   # Abfrage aktuelle PWM
    value = parse_response_value(resp)
    if value:
        print(pwm_code_to_amps(value))
        return
    print(0)


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: abl_evcc_bridge.py status|enabled|enable|maxcurrent|current [arg]")

    action = sys.argv[1]
    if action == "status":
        cmd_status()
    elif action == "enabled":
        cmd_enabled()
    elif action == "enable":
        if len(sys.argv) < 3:
            sys.exit("enable benoetigt Parameter true/false")
        cmd_enable(sys.argv[2].strip().lower() in ("true", "1", "on"))
    elif action == "maxcurrent":
        if len(sys.argv) < 3:
            sys.exit("maxcurrent benoetigt Parameter <Ampere>")
        cmd_maxcurrent(sys.argv[2])
    elif action == "current":
        cmd_current()
    else:
        sys.exit(f"Unbekannte Aktion: {action}")


if __name__ == "__main__":
    main()
