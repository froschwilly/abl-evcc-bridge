# ABL-Sursum EVCC-Pilotmodul in evcc einbinden – Bridge-Skript für ältere Wallboxen

*Erfahrungsbericht und Workaround · Stand November 2026 · ohne Gewähr, kein Support*

## ⚠️ Wichtige Hinweise – bitte zuerst lesen

- **Nur einphasig getestet.** Alles hier wurde ausschließlich im einphasigen Betrieb (6–32 A) entwickelt und getestet. Eine Phasenumschaltung (1↔3-phasig) wird vom Skript nicht unterstützt und wurde nicht getestet. Wer dreiphasig lädt, muss Stromgrenzen, PWM-Berechnung und die evcc-Phaseneinstellung selbst anpassen und prüfen.
- **Workaround ohne Gewähr, ohne Support.** Dies ist ein privat erarbeiteter Workaround für eine seit Jahren nicht mehr offiziell unterstützte Hardware-Kombination. Bereitstellung „wie besehen“ (as-is), ohne Garantie auf Funktion, Sicherheit oder Vollständigkeit und ohne Zusage auf laufende Unterstützung. Nutzung auf eigene Verantwortung. Es geht um eine 230-V-Ladeanwendung; Arbeiten an der Installation gehören in die Hand einer Elektrofachkraft.
- **Getestet:** Mit der aktuellen Skriptversion funktionieren in evcc „Smart“ (mit und ohne „Dauerhaft laden“), „Aus“ und „Schnell“. Beim Anstecken bleibt die Box gesperrt, bis evcc sie freigibt. Einzelheiten im Abschnitt „Empfohlener Betrieb“.
- Diese Doku soll dir Wochen an Fehlersuche ersparen. Sie ersetzt nicht das eigene Verständnis der Hardware. Für dieses Modul ist ein nativer evcc-Treiber in Arbeit (offener Pull Request #33644 im evcc-Repository, Stand Oktober 2026). Sobald er in einer evcc-Version enthalten ist, ist er dieser Bridge vorzuziehen.

## Getestete Umgebung

| Komponente | Stand |
| --- | --- |
| Wallbox | E.ON Basisbox Kombi (Baujahr 2013) mit ABL-Sursum-EVCC-Pilotmodul, Firmware V2.3 |
| RS485 ↔ Ethernet | ET69C02 DC 12 V Ethernet-zu-RS485-Konverter und Ebyte NA111-M (laut Autor identische Weboberfläche und Einstellungen), jeweils im transparenten Modus als TCP-Server |
| evcc | Raspberry-Pi-Image (Konfiguration über die Web-UI, Daten in evcc.db), Nightly-Build mit Float-Setter für das script-Plugin |
| Fahrzeug | Tesla (zu einer Renault Zoe Q210 siehe „Bekannte Eigenheiten“) |
| Phasen | einphasig, 6–32 A |

Nicht getestet: dreiphasiger Betrieb, andere Firmware-Versionen des Moduls, andere RS485-Adapter, andere Fahrzeuge.

## Worum geht es?

In manchen älteren Wallboxen (z. B. E.ON- und ABL-Sursum-Boxen) steckt ein „EVCC“-Pilotmodul von ABL-Sursum. Der Name hat nichts mit der Software evcc zu tun, das ist reiner Zufall. Das Modul spricht kein Modbus, sondern ein ASCII-Protokoll über RS485: Befehle werden als Text gesendet und als Text beantwortet. evcc kennt dieses Protokoll nicht von Haus aus.

Die Lösung hier: ein kleines Python-Skript, das evcc als „benutzerdefinierte Wallbox“ (`type: custom`, `source: script`) aufruft. Das Skript übersetzt die evcc-Aufrufe in ABL-Befehle und spricht über einen transparenten TCP/IP-RS485-Adapter mit dem Modul.

## Architektur

```
evcc (Raspberry Pi)
  └─ custom charger, source: script
       └─ abl_evcc_bridge.py ──TCP──> Ethernet-RS485-Konverter (z. B. 192.168.1.91:552)
                                          └─ RS485 (A/B/M) ──> ABL-EVCC-Modul in der Wallbox
```

evcc ruft das Skript pro Aktion kurz auf (`status`, `enabled`, `enable`, `maxcurrent`). Das Skript baut den ABL-Befehl, schickt ihn per TCP, liest die Antwort und gibt das Ergebnis auf stdout aus. Es soll bewusst nur ein Übersetzer bleiben und keine eigene Ladelogik enthalten.

**Wichtig: Es darf nur einen RS485-Master geben.** Der Bus verträgt nur einen Sender. War die Box vorher an einem Datenlogger angeschlossen, muss dieser vom Bus getrennt sein.

### Warum nicht über den vorhandenen Datenlogger?

Manche Datenlogger und Steuerungen bieten ein eigenes TCP-Gateway, über das sich ABL-Befehle durchreichen lassen. Das sah zunächst ideal aus, funktionierte in meinem Aufbau aber nur, solange die Ladeautomatik des Loggers für die Box aktiv war. Nach dem Abschalten kam nur noch „NO WALLBOX“ zurück, und mit aktiver Automatik regeln zwei Systeme gleichzeitig den Ladestrom. Deshalb: eigener Adapter, exklusiv an der Wallbox.

## Adapter-Einstellungen (ET69C02 / Ebyte NA111-M)

| Einstellung | Wert |
| --- | --- |
| Work mode | TCP Server |
| Local IP / Local port | frei wählbar (Beispiel: 192.168.1.91 / 552) |
| MODBUS TCP to RTU | **Close** |
| Modbus mode | **disable** |
| Baud rate | **38400** |
| Data bits / Parity / Stop bit | 8 / None / 1 |
| Flow control | NONE |

Hinweise:

- **Modbus-Gateway unbedingt ausgeschaltet lassen.** Laut Handbuch verwirft der Adapter bei aktivem Modbus-Gateway alles, was kein gültiger Modbus-Frame ist. Das ABL-Protokoll ist reines ASCII und käme nie beim Modul an.
- **Baudrate umstellen:** ab Werk steht der Adapter auf 115200, das Modul braucht 38400 (8N1, kein Flow Control).
- Im TCP-Server-Modus reicht der Adapter seriell empfangene Daten an **alle** verbundenen TCP-Clients weiter (bis zu 6). Eine parallel zu evcc offene `nc`-Sitzung sieht deshalb fremde Antworten, gelegentlich auch `ERR`. Manuelle Tests mit `nc` am besten nicht parallel zu laufendem evcc machen.
- Das Webkonfigurations-Passwort ist laut Handbuch ab Werk `123456`, bitte ändern. Nach der Konfiguration den Adapter neu starten.
- Laut ABL-Datenblatt hat die RS485-Schnittstelle des Moduls keinen internen Abschlusswiderstand (120 Ω an beiden Netzwerkenden), keine galvanische Trennung, und die Klemme M ist intern mit FE verbunden. Das beim Verdrahten beachten. In meinem Aufbau traten anfangs Störzeichen auf der Leitung auf. Seit „Short connection = 60 s“ am Adapter (ET01, Firmware 9013-8-17) und der Skriptversion mit Wiederholungen gab es keine Fehler mehr. Ein ursächlicher Zusammenhang ist nicht belegt.

Erster Test (Firmware abfragen):

```
printf '!1 01\r\n' | nc 192.168.1.91 552
>1 01 V2.3
```

Mit `nc -q1` beendet sich `nc` nach der Antwort von selbst, wenn die nc-Variante das unterstützt. Sonst mit Strg+C abbrechen oder (printf '!1 01\\r\\n'; sleep 1) | nc 192.168.1.91 552 verwenden.

## Das Protokoll in Kürze

Format: `!<Adresse> <Funktion> [<Parameter>]` plus CR LF. Antworten beginnen mit `>`. Parameter sind immer **vierstellig mit führenden Nullen** (`0100`, nicht `100`).

| Funktion | Befehl | Bedeutung |
| --- | --- | --- |
| 01 | `!1 01` | Firmware-Version |
| 02 | `!1 02` | Zustand (Tabelle unten) |
| 11 | `!1 11` | aktuelle PWM = eingestellter Sollstrom |
| 12 | `!1 12 xxxx` | PWM setzen = Ladestrom vorgeben |
| 23 | `!1 23` | Moduladresse abfragen |

**Zustandscodes (Funktion 02):** `0000` = A (kein Fahrzeug) · `0013` = B1 · `0004` = B2 (Fahrzeug verbunden, bereit) · `0005` = C · `0006` = D (lädt) · `0009` = B' (Ladung vom Fahrzeug beendet/unterbrochen) · `0017` = A' (nur per RS485 setzbare Pause) · `0033`/`0035`/`0037`/`0039` = Fehler · `0255` = MANUAL · 0012 = Fahrzeug erkannt bei gesetztem bBreakCharge-Flag (im Datenblatt nicht aufgeführt, beobachtet).

**Ladestrom (Funktion 12):** `0080` bis `0970` entsprechen 8,0 bis 97,0 % Tastverhältnis in 0,1-%-Schritten. `0999` bedeutet „Laden verboten“. Laut Auskunft von ABL (2014) wird 0999 nur in den Zuständen C und D angenommen, in allen anderen Zuständen antwortet das Modul mit >n 12 ERR und speichert nichts. Der Befehl !n 14 schaltet laut ABL die PWM tatsächlich aus (im Handbuch steht dort fälschlich „einschalten“, der Befehl ist laut Handbuch nur im Zustand MANUAL verfügbar).

**Sperr-Flag (Funktionen 27/28/29):** `!1 27` setzt das bBreakCharge-Flag, `!1 28` löscht es, `!1 29` fragt es ab (`0001` = gesetzt, `0000` = gelöscht). Laut Datenblatt hält ein gesetztes Flag den Ladevorgang in B1 an. ABL beschrieb es 2014 als ähnlich wie einen offenen E1-Eingang: Wird das Flag vor dem Anstecken gesetzt, erkennt das Modul das Fahrzeug, lädt aber erst nach dem Löschen des Flags. Ein Setzen während der Ladung hat keinen Einfluss. Bei diesem Aufbau sind E1 und E2 dauerhaft geschlossen (Anschlussbeispiel „Ladekabel ohne externe Freigabe“ in der ABL-Anleitung).

**CP-Spannung (Funktion 07):** `!1 07` liefert den ADC-Wert der positiven CP-Spannung, U \[V\] = 0,017 × Wert. Gemessen: etwa 12,2 V (Wert 0718) ohne Fahrzeug, etwa 9,0 V (0530) bei angestecktem Fahrzeug, etwa 5,9 V (0347) in Zustand C. Das Skript nutzt das, um bei gesetztem Flag das Abstecken zu erkennen.

Umrechnung: **I \[A\] = PWM-Wert × 0,06**. Im ABL-Handbuch steht „PWM · 0,6“, das ist ein Tippfehler, denn der Wert ist in 0,1-%-Einheiten angegeben. Beispiele: 6 A = `0100`, 16 A = `0267`, 32 A = `0533`. Das ergibt Schritte von etwa 0,06 A. Die im Handbuch zusätzlich aufgeführten Festwerte (`0000`–`0009` für 6/10/13/16/… A) nutze ich nicht, damit die Regelung fein bleibt.

**Achtung:** `!1 11` liefert den *eingestellten* Wert, keinen gemessenen Strom. Das Modul hat keine Strommessung. Nach dem Sperren (`0999`) liefert die Abfrage ebenfalls `0999` zurück, das Skript zeigt dann 0 A an.

**Moduladresse:** ab Werk 0 (Broadcast). Mein Modul antwortete auf `!0 23` mit `>1 23 0001`, also Adresse 1. Mit Adresse 0 funktioniert die Ansprache ebenfalls, solange nur ein Modul am Bus hängt.

## Installation des Skripts

1. Python 3 muss vorhanden sein (nur Standardbibliothek, kein `pip` nötig).
2. Skript nach `/usr/local/bin/abl_evcc_bridge.py` kopieren und ausführbar machen: `sudo chmod 755 /usr/local/bin/abl_evcc_bridge.py`
3. Konfiguration am Dateianfang anpassen: `ADAPTER_HOST`, `ADAPTER_PORT`, `MODULE_ADDR`, `MIN_CURRENT_A` (6), `MAX_CURRENT_A` (32).
4. Auf dem evcc-Rechner testen:

```
python3 /usr/local/bin/abl_evcc_bridge.py status        # A / B / C / F
python3 /usr/local/bin/abl_evcc_bridge.py current
python3 /usr/local/bin/abl_evcc_bridge.py enable true
python3 /usr/local/bin/abl_evcc_bridge.py maxcurrent 10
python3 /usr/local/bin/abl_evcc_bridge.py current       # ca. 10.02
python3 /usr/local/bin/abl_evcc_bridge.py enable false
```

`maxcurrent` wird bewusst ignoriert, solange die Box gesperrt ist (Flag gesetzt, siehe „Verhalten des Skripts“). Beim manuellen Testen also immer erst `enable true`.

## evcc-Konfiguration

In der evcc-Web-UI: **Konfiguration → Ladepunkt hinzufügen → Benutzerdefinierte Wallbox** und folgenden Block einfügen:

```yaml
status:
  source: script
  cmd: /usr/bin/python3 /usr/local/bin/abl_evcc_bridge.py status
  timeout: 15s
enabled:
  source: script
  cmd: /usr/bin/python3 /usr/local/bin/abl_evcc_bridge.py enabled
  timeout: 15s
enable:
  source: script
  cmd: /usr/bin/python3 /usr/local/bin/abl_evcc_bridge.py enable ${enable}
  timeout: 15s
maxcurrent:
  source: script
  cmd: /usr/bin/python3 /usr/local/bin/abl_evcc_bridge.py maxcurrent ${maxcurrent}
  timeout: 15s
maxcurrentmillis:
  source: script
  cmd: /usr/bin/python3 /usr/local/bin/abl_evcc_bridge.py maxcurrent ${maxcurrentmillis:%.2f}
  timeout: 15s
```

Danach im Ladepunkt: **Min. Strom 6 A, Max. Strom 32 A, Phasen 1.**

Hinweise:

- `maxcurrentmillis` (Ladestrom mit Nachkommastellen) braucht eine evcc-Version, die den Float-Setter für das script-Plugin enthält (Pull Request #33426). Ältere Versionen melden beim Speichern `invalid plugin source for type float: script`. Dann den `maxcurrentmillis`-Block weglassen, evcc regelt dann in ganzen Ampere.
- Wer evcc über eine `evcc.yaml` konfiguriert, trägt dieselben Schlüssel unter `chargers:` mit `type: custom` ein.
- Das Aktualisierungsintervall (Konfiguration → Regelverhalten, Standard 30 s) bestimmt, wie schnell evcc nachregelt. evcc rät von Werten unter 10 s ab. Die timeout-Werte stehen bewusst auf 15 s, weil das Skript bei Übertragungsfehlern jeden Befehl bis zu dreimal versucht.

### Statuszuordnung

| Zustand des Moduls | Meldung an evcc |
| --- | --- |
| A, A', 0012 bei abgestecktem Fahrzeug (CP-Spannung ab etwa 10,5 V) | A (kein Fahrzeug) |
| B1, B2, B', 0012, MANUAL, sowie C/D bei gesetztem Flag und PWM 0999 (hängendes C) | B (verbunden, lädt nicht) |
| C, D | C (lädt) |
| Fehlerzustände | F |

### Verhalten des Skripts

- `status`: meldet A/B/C/F aus dem Zustandscode. Zustand 0012 (Fahrzeug erkannt, Flag gesetzt) wird als B gemeldet, bei einer CP-Spannung ab etwa 10,5 V (`!1 07` ab 620) als A (abgesteckt). Zustand C/D bei gesetztem Flag und PWM `0999` (hängendes C nach dem Sperren) wird als B gemeldet (Schalter `REPORT_LOCKED_C_AS_B`).
- `enable true`: behält einen bereits vorgemerkten Zielstrom, sonst setzt es den Mindeststrom (6 A), und löscht danach das bBreakCharge-Flag (`!1 28`). Die Reihenfolge verhindert, dass ein wartendes Fahrzeug mit einem ungültigen Stromwert startet.
- `enable false`: setzt zuerst das Flag (`!1 27`, verhindert neue Ladestarts) und dann PWM `0999` (stoppt eine laufende Ladung, das Flag allein tut das nicht).
- `maxcurrent x`: setzt den Strom entsprechend, begrenzt auf 6–32 A. Bei gesetztem Flag wird der Strom nur vorgemerkt, wenn das Modul nicht gerade Zustand C meldet (sonst würde eine hängende Ladung wieder anlaufen). evcc sendet beim Start zuerst `maxcurrent` und dann `enable`, im Modus „Schnell“ nur einmal. Ohne Vormerken blieb die Ladung bei 6 A hängen. Bei ungültiger Flag-Abfrage passiert nichts.
- `enabled`: liest das Flag direkt aus dem Modul (`!1 29`): gelöscht = `true`, gesetzt = `false`. Es gibt keine Statusdatei. Bei ungültiger Antwort beendet sich das Skript mit Fehler, evcc überspringt dann den Abruf.
- Übertragung: Jeder Befehl wird bis zu dreimal versucht. Eine Antwort wird nur akzeptiert, wenn sie zum Funktionscode passt, Störzeichen davor oder dazwischen werden ignoriert. Gleichzeitige Aufrufe laufen nacheinander (Sperrdatei `/tmp/abl_evcc_bus.lock`).
- Jeder `enable`/`maxcurrent`-Aufruf wird mit Zeitstempel (UTC) in `/tmp/abl_evcc_debug.log` protokolliert (`tail -f` zum Mitlesen). `/tmp` wird beim Neustart geleert.
- Netzwerkfehler führen nicht zum Absturz: `status` meldet F, `current` 0 A, `enabled` beendet sich mit Fehler, die Ursache steht auf stderr.

## Empfohlener Betrieb

Mit der aktuellen Skriptversion funktionieren alle evcc-Modi: „Smart“ (mit und ohne „Dauerhaft laden“), „Aus“ und „Schnell“. Am einfachsten ist **„Smart“ mit „Dauerhaft laden – keine Unterbrechungen, min. 6 A“** (früher „Min+PV“): evcc sperrt den Ladepunkt dann nicht bei fehlendem Überschuss und führt nur den Strom zwischen 6 und 32 A nach. Der Preis: Bei zu wenig Überschuss wird mit mindestens 6 A aus dem Netz geladen. Wer nur mit Überschuss laden will, nimmt „Smart“ ohne diese Option.

**Wie die Sperre funktioniert.** Das ABL-Modul kennt keine saubere, vom Verbindungsstatus getrennte Ebene für „gesperrt/freigegeben“. Zwei Mittel ergeben zusammen eine brauchbare Sperre:

- Das bBreakCharge-Flag (`!1 27`) verhindert, dass ein angestecktes Fahrzeug zu laden beginnt. Das Modul meldet dann den Zustand 0012. Eine bereits laufende Ladung stoppt das Flag nicht.
- PWM `0999` stoppt eine laufende Ladung, wird laut ABL aber nur in den Zuständen C und D angenommen.

Ohne das Flag bot das Modul einem neu angesteckten Fahrzeug den zuletzt gesetzten Strom an (0243 = 14,6 A, obwohl der Default 0533 = 32 A war). Die Box lud selbst im Modus „Aus“ ein bis zwei Minuten mit 1,4 bis 3,6 kW, bis evcc beim nächsten Status-Abruf sperrte. Mit dem Flag bleibt sie gesperrt, bis evcc freigibt.

**Zwei Eigenheiten, die das Skript ausgleicht:**

- Nach dem Sperren meldet das Modul noch minutenlang Zustand C, obwohl kaum Strom fließt („hängendes C“). evcc würde „disabled but charging“ melden und zwischen `enable(true)` und `enable(false)` pendeln. Das Skript meldet C deshalb als B, solange das Flag gesetzt und PWM `0999` ist.
- Bei gesetztem Flag bleibt der Zustand 0012 nach dem Abstecken stehen. Das Skript prüft dann die CP-Spannung und meldet bei etwa 12 V den Zustand A (abgesteckt).

**Testergebnisse** (Tesla, einphasig, Stand 11.11.2026):

| Szenario | Ergebnis |
| --- | --- |
| „Smart“ mit Überschuss, anstecken | Ladung startet mit 6 A, evcc regelt dem Überschuss nach (bis über 20 A) |
| „Smart“ ohne Überschuss, anstecken | Box bleibt gesperrt, Status B, kein Pendeln |
| „Aus“, anstecken | Box bleibt gesperrt (Tesla-App: „Ladefehler – Kein Strom“) |
| „Aus“, anstecken, abstecken | evcc meldet beim nächsten Abruf „nicht verbunden“ |
| „Schnell“, anstecken | 32 A, rund 7,4 kW |
| Während der Ladung auf „Aus“ | Leistung fällt auf etwa 8 W, Status wechselt auf B, keine Warnung |
| evcc-Neustart bei laufender Ladung | unkritisch |

**Wichtig:** Das Flag überlebt einen Stromausfall der Box. Wer das Skript abschaltet oder evcc entfernt, muss es mit `!1 28` löschen, sonst startet kein Fahrzeug mehr. Nach dem Abstecken springt evcc selbst auf seinen Standardmodus zurück (Einstellung im Ladepunkt).

## Bekannte Eigenheiten

- **evcc-Warnung `charger logic error: disabled but charging` bzw. `charger out of sync`.** Sie trat mit der reinen PWM-Sperre bei jeder Pause auf, weil das Modul nach dem Sperren noch minutenlang C meldete. Mit der aktuellen Skriptversion meldet das Skript dieses hängende C als B, die Warnung und das Pendeln entfallen (in den Tests vom 11.11. keine Warnung). Bei älteren Skriptversionen oder mit `REPORT_LOCKED_C_AS_B = False` kann sie weiter auftreten.
- **Tesla-App: „Kommunikation mit Ladeausrüstung abgebrochen“** beim harten Unterbrechen der Ladung (z. B. Wechsel nach Zustand A'). Beobachtet, harmlos.
- **Tesla-App zeigt „Wird geladen“ bei 0/0 A**, obwohl nichts fließt. Das ist die Darstellung der App, solange das Modul Zustand C meldet.
- **`ERR` in Antworten** wurde beim manuellen Testen während laufender evcc-Abfragen beobachtet (siehe Hinweis zu mehreren TCP-Clients). Das Skript wertet es nicht aus.
- **Renault Zoe Q210 (Erfahrungswert des Autors ab 2013, mit diesem Skript nicht getestet):** Dieses Fahrzeug startet die Ladung erst ab etwa 8 A einphasig bzw. 13 A dreiphasig. Wer so ein Fahrzeug hat, stellt in evcc den Mindeststrom entsprechend höher ein.
- **Feine Stromschritte** gibt es nur mit `maxcurrentmillis` (siehe evcc-Konfiguration).

## Sackgassen – das musst du nicht nochmal probieren

1. **TCP-Gateway eines vorhandenen Datenloggers als Brücke:** funktioniert nur, solange dessen eigene Ladeautomatik aktiv ist, dann Doppelsteuerung (siehe oben).
2. **Modbus-Gateway im Adapter:** verwirft die ASCII-Befehle.
3. **Pause über Zustand A' (`!1 30` / `!1 31`):** Das Modul bleibt in A', bis per RS485 `!1 31` kommt, und kehrt beim Abstecken nicht von selbst nach A zurück. Im evcc-Betrieb meldet das Skript dann „nicht verbunden“, und evcc gibt einen Ladepunkt ohne Fahrzeug nie wieder von selbst frei. In meinen Tests startete die Ladung erst nach manuellem `!1 31`, auch bei reichlich Überschuss. Auch eine Variante, die erst nach längerer Sperrdauer nach A' wechselt, hatte dieses Problem. A' funktioniert nur, wenn dieselbe Instanz pausiert und wieder aufweckt, wie es bei einer eigenen Zeitsteuerung (Pause bei Sonnenuntergang, Fortsetzung bei Sonnenaufgang) funktionierte.
4. **Sperren nur per PWM `0999`:** wirkt nur in den Zuständen C und D (sonst ERR) und hebt den vom Modul gemeldeten Zustand nicht aus C heraus (siehe „Empfohlener Betrieb“).

## Zum Vergleich: wie macht es ein nativ unterstützter Charger?

Bei einem Tinkerforge WARP4 bleibt der Status bei angestecktem Fahrzeug ohne Überschuss stabil auf B, `charge power` bei 0 W, und die Sperre erscheint getrennt als „Erlaubter Ladestrom: Blockiert durch externe Steuerung“. Es gibt keine Warnungen und kein Hin und Her, und es fließt von Anfang an kein Strom. Die Tesla-App zeigt dann „Ladegerät nicht bereit“. Dem ABL-Modul fehlt eine solche getrennte Freigabe-Ebene. Das bBreakCharge-Flag kommt ihr nahe, braucht aber die beschriebenen Ausgleiche im Skript.

## Fehlersuche

| Symptom | Mögliche Ursache / Abhilfe |
| --- | --- |
| `status` liefert F, stderr zeigt Verbindungsfehler | IP/Port des Adapters und Netzwerk prüfen; Adapter nicht im Webkonfigurations-Modus (neu starten) |
| `status` liefert F ohne Fehlermeldung | keine gültige Antwort (evcc loggt dann „invalid status: F“ und überspringt den Abruf, das tritt auch bei gleichzeitigem Zugriff durch einen zweiten Client auf): Baudrate 38400 prüfen, Modbus-Gateway noch aktiv, RS485-Adern A/B vertauscht (einfach tauschen), Moduladresse |
| Datenmüll statt Antwort | Baudrate/Parität am Adapter falsch (Werkseinstellung 115200). Gelegentliche Störzeichen fängt das Skript durch Wiederholungen ab. Bei häufigen Störungen Abschlusswiderstand (120 Ω), Vorspannung, Verdrahtung und die Nähe zu 230-V-Leitungen prüfen. |
| Ladestrom bleibt bei 6 A | `tail -f /tmp/abl_evcc_debug.log`: fordert evcc überhaupt mehr an? Phasen auf 1? Vorzeichen des Netzzählers? Flag abfragen (`!1 29`): `0001` bedeutet gesperrt, dann werden `maxcurrent`-Aufrufe vorgemerkt und erst mit „enable true“ übernommen (Debug-Log prüfen). |
| `current` zeigt unplausible Werte (z. B. 59.94) | ältere Skriptversion: der Sonderwert `0999` wurde als Strom gerechnet; aktuelle Version zeigt 0 |
| evcc meldet `invalid plugin source for type float: script` | evcc zu alt für `maxcurrentmillis`: Block entfernen oder evcc aktualisieren |

## Quellen und Dank

- ABL-Sursum: Montage- und Bedienungsanleitung EVCC, Dok.-Nr. 03 0009 01 D. Das Dokument ist urheberrechtlich geschützt und nur noch in Archiven zu finden, z. B. über die Wayback Machine (web.archive.org/web/20160122105853/http://www.abl-sursum.com/global/downloads/bedienungsanleitungen/EVCC.pdf) oder bei manualslib (www.manualslib.de/manual/785244/Abl-Sursum-Evcc.html). Diese Doku gibt nur die für die Integration nötigen Fakten in eigenen Worten wieder.
- Herstellerhandbuch des Serialservers: Ebyte NT1-B User Manual V1.3 (Serialserver-Familie NA11x/NT).
- evcc: Issue #33424 und Pull Request #33426 (Float-Setter für das script-Plugin), vom Maintainer innerhalb eines Tages umgesetzt. Vielen Dank dafür!

## Haftungsausschluss und Lizenz

Die Software und diese Dokumentation werden ohne jegliche Gewährleistung bereitgestellt. Der Autor haftet nicht für Schäden, die aus der Nutzung entstehen. Rückmeldungen sind willkommen, ein Anspruch auf Support besteht nicht.

Skript und Dokumentation wurden gemeinsam mit dem KI-Assistenten Claude (Anthropic, claude.ai) erarbeitet. Getestet wurde ausschließlich am realen Aufbau des Autors. Bitte den Code vor dem Einsatz selbst prüfen.

Lizenz: MIT · Copyright (c) 2026 frosch.willy

Autor: frosch.willy · Rückmeldungen bitte im jeweiligen Beitrag oder Thread, kein Anspruch auf Support
