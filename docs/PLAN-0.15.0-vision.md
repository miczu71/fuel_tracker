# Fuel Tracker 0.15.0 — łańcuch providerów skanera paragonów

## Context

**Objaw:** dodanie paragonu kończyło się „błędem komunikacji z LLM Vision".

**Przyczyna (potwierdzona empirycznie):** konto Google stojące za jedynym providerem llmvision
(`01JN36MXA0ME61G2RV6FSRT6VX`) nie miało kredytów. Log HA z 14.08 13:54:35 — dokładnie moment,
w którym add-on zwrócił 502:

```
custom_components/llmvision/providers.py:547
ServiceValidationError: Your prepayment credits are depleted.
```

Ścieżka: Google odrzuca → llmvision rzuca `ServiceValidationError` → wyjątek ucieka z handlera HA
(`aiohttp.server: Error handling request`) → REST oddaje gołe HTTP 500 → `ha_client.call_service()`
zwraca `None` → add-on 502.

**Stan po przełączeniu konta na free tier (2026-08-14): przyczyna ustąpiła.** Ten sam klucz
z wpisu llmvision, przetestowany na realnym paragonie z `tests/fixtures/` produkcyjnym
`receipts.PROMPT` i `receipts.STRUCTURE`:

| Test | Wynik |
|---|---|
| `GET /v1beta/models` | HTTP 200; `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-2.5-flash` dostępne |
| `gemini-3.1-flash-lite` + obraz + `response_schema` | **HTTP 200, 1,9 s** — odczyt 1:1 z paragonem (2026-07-03 15:56 / 31462 km / 52,47 L / 357,85 PLN / ORLEN Będzino) |
| `gemini-2.5-flash` + obraz | HTTP 200, 5,1 s — identyczny odczyt |
| **`gemini-2.5-flash-lite`** (drugi model z `MODELS`) | **HTTP 404 — „no longer available to new users"** |

Drugi klucz, wklejony przez użytkownika (`AQ.Ab8…`), przeszedł te same testy — oba są sprawne.

**Skutek: skaner działa dziś bez żadnej zmiany kodu.** Reconfigure llmvision jest niepotrzebny
(i był ryzykowny — `entry_id` jest zahardkodowany w `scripts.yaml:319` i `automations.yaml:2391`,
więc usuń-i-dodaj by je zepsuło). Przy okazji odżyły oba te wywołania: skrypt „Liczba samochodow
na parkingu" i blueprint `event_summary` dla `camera.front` — leżały z tego samego powodu.

### Co mimo to zostaje do naprawy

Dwa realne błędy w kodzie, odkryte przy diagnozie — niezależne od stanu konta Google:

1. **Martwy fallback modeli.** `receipts.analyze()` miał próbować kolejny model po porażce.
   Nie próbuje: `call_service()` na HTTP 500 zwraca `None`, a `analyze()` natychmiast rzuca
   `ReceiptError`. Pętla `for model in MODELS` wykonuje się **zawsze tylko raz** — potwierdzone
   logiem (jeden wpis `-> HTTP 500` na jedną próbę skanu). Fallback z 0.5.1 nigdy nie zadziałał
   dla przypadku, dla którego powstał.
2. **Komunikat gubi powód.** UI pokazało „Usługa llmvision nie odpowiedziała", podczas gdy
   provider podał konkretny, akcjonowalny powód (wyczerpane kredyty). Diagnoza wymagała
   grzebania w logach HA — dokładnie tego, czego użytkownik na telefonie nie zrobi.

Plus jedna bomba z opóźnionym zapłonem: **`gemini-2.5-flash-lite` w `receipts.py:34` jest martwy**
(404 dla nowych kluczy) — jedyny „zapasowy" model add-onu nie istnieje.

**Cel:** łańcuch providerów odporny na awarię pojedynczego ogniwa + prawdziwy powód błędu w UI.

**Decyzje użytkownika:** klient wprost w add-onie (nie provider „Custom OpenAI" w llmvision);
**Google jako primary**, **freellmapi jako fallback**; ścieżka llmvision zostaje jako ostatnie ogniwo.

---

## Krok 0 — vision w freellmapi — WYNIK: POZYTYWNY

Sonda na `tests/fixtures/receipt_orlen_fleet.jpg` przez `http://192.168.0.106:3003/v1`
(klucz z opcji add-onu nokia_tracker), `image_url` z `data:` URI + `response_format: json_schema`
+ `strict: true`, **bez** `additionalProperties` w schemacie (jak `receipts.STRUCTURE`):

| Model na routerze | Wynik |
|---|---|
| `gemini-3.1-flash-lite` | **HTTP 200, 4,3 s — odczyt identyczny z bezpośrednim Gemini API** (ten sam backend, inny klucz/limit) |
| `qwen2.5-vl-72b` | HTTP 429 rate-limited (przejściowe, nieistotne — mamy działającego kandydata) |
| `nemotron-nano-12b-vl` | HTTP 200, ale **zły odczyt** (halucynacja treści paragonu) i wolny (39 s) — odrzucony |

**Wniosek:** `local_llm_model` domyślnie `gemini-3.1-flash-lite` (zgodnie z domyślną wartością
w nokia_tracker); schemat `STRUCTURE` nie wymaga wariantu z `additionalProperties` — ten sam
kształt działa na obu ogniwach. Ogniwo lokalne w pełni sprawne, nieblokujące już niczego.

## Krok 1 — plan do repo

Ten plik: `docs/PLAN-0.15.0-vision.md`, zapisany przed pierwszą linią kodu implementacji
(CLAUDE.md: plan żyjący tylko w transkrypcie ginie przy kompaktowaniu).

## Krok 2 — `fuel_tracker/vision.py` (TDD)

Jeden moduł, dwa klienty, wspólny kontrakt: `dict` albo `VisionError` z **prawdziwą treścią błędu**.

**`call_gemini()`** — primary. REST `v1beta/models/<model>:generateContent`, dokładnie ten kształt,
który przeszedł testy powyżej: `inline_data` + `generationConfig.response_schema` +
`response_mime_type`. Nagłówek `x-goog-api-key` (zweryfikowany; `?key=` też działa, ale nie
wkładamy klucza do URL-a). Wzór: `nokia_tracker/nokia_tracker/ai/gemini.py`.

**`call_local()`** — fallback. Wzorowany 1:1 na `nokia_tracker/nokia_tracker/ai/openai_compat.py`,
z jego **zmierzoną** wiedzą o routerze (nie z założeń), plus zmierzone teraz wsparcie obrazów:
- `max_tokens` podnoszone do min. **1500** (przy 300 router zwracał 502 „truncated JSON" —
  tokeny reasoningu liczą się przed treścią),
- **502 = błąd upstreamu, retryable** (nie 429) → 2–3 próby z backoffem (mały helper lokalnie,
  nie kopiujemy całego `ratelimit.py`),
- obraz jako `data:image/jpeg;base64,…` w `content`-parts (`image_url`), zweryfikowane w Kroku 0,
- model domyślny `gemini-3.1-flash-lite`.

Wspólne: mimetype z rozszerzenia (ta sama biała lista co `receipts._ALLOWED_EXT`), parsowanie
przez istniejące `receipts.extract_json()` (nie duplikujemy — modele lubią płoty ```json),
komunikat błędu w formacie `f"{provider}: HTTP {code} — {text[:300]}"`.

Zależności: tylko `requests` (już w `requirements.txt`). Bez Pillow — fixture 152 KB przeszedł
na obu ogniwach bez problemu; przy realnym zdjęciu z telefonu to osobna decyzja, nie na ślepo.

## Krok 3 — `receipts.analyze()` jako łańcuch providerów

Wzór: `nokia_tracker/nokia_tracker/ai/provider.py` — ogniwo, które zawiedzie, oddaje głos
następnemu; błąd dopiero gdy padną wszystkie.

```
1. gemini     → vision.call_gemini()  dla każdego modelu z MODELS   (primary)
2. local      → vision.call_local()   (pomijane, gdy base_url/klucz puste)
3. llmvision  → istniejąca ścieżka    dla każdego modelu z MODELS
```

- **`MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")`** — podmiana martwego
  `gemini-2.5-flash-lite` (404, zmierzone). Oba nowe zweryfikowane na realnym paragonie,
- ogniwo llmvision idzie do kolejnego modelu **także po HTTP 500/None** → naprawa błędu nr 1,
- powody porażek zbierane i sklejane w `ReceiptError`, np.
  `„gemini: HTTP 429 — quota; freellmapi: HTTP 502 — provider_error; llmvision: HTTP 500"`
  → naprawa błędu nr 2. Front już renderuje `data.error` z 502 (`static/app.js` ~285),
  więc **zero zmian w JS**,
- brak wpisu llmvision w HA przestaje być błędem twardym — to po prostu brak trzeciego ogniwa
  (dziś `find_config_entry` → `ReceiptError` blokuje wszystko),
- `normalize()`, `STRUCTURE`, `PROMPT` bez zmian — wszystkie ogniwa dostają to samo.

## Krok 4 — konfiguracja

Nowe opcje Supervisora, spójnie z kredencjałami Drivvo (ENV z `/data/options.json`, **nigdy**
w tabeli `settings`, nigdy w repo):

| Plik | Zmiana |
|---|---|
| `config.yaml` | `gemini_api_key` (`password`), `gemini_model`, `local_llm_base_url`, `local_llm_api_key` (`password`), `local_llm_model` + `schema` |
| `run.sh` | pięć `export` z `jq`, wzorem `DRIVVO_PASSWORD` |
| `main.py` | odczyt przez `_env()`, przekazanie do `create_app()` (konwencja DI jak `ha_state`/`ha_call_service`) |
| `web.py` | przekazanie configu do `receipts.analyze()` |

W `gemini_api_key` może pójść dowolny z dwóch sprawnych kluczy — ten z llmvision albo nowy.
Puste pole = ogniwo pomijane, łańcuch spada na kolejne (czyli zachowanie sprzed 0.15.0).

Poza zakresem (świadomie): karta w Ustawieniach z selectem modeli — zmiana modelu = restart
add-onu, jak przy Drivvo.

## Krok 5 — testy (TDD, red-green na każdym kroku)

Nowy `tests/test_vision.py` + rozszerzenie `tests/test_receipts.py`, wszystko na mockach
`requests` (zero ruchu sieciowego w CI):

- poprawna odpowiedź obu klientów → sparsowany dict,
- HTTP 502/429/401 → `VisionError` **z treścią providera w komunikacie** (asercja na treść),
- 502 → retry, potem sukces; `max_tokens` < 1500 podnoszone,
- **regresja martwego fallbacku**: pierwszy model zwraca 500 → drugi MUSI zostać spróbowany
  (dziś ten test jest czerwony),
- łańcuch: gemini pada → local przejmuje; oba padają → llmvision; ogniwo bez configu pomijane
  bez błędu; wszystkie padają → `ReceiptError` z wszystkimi powodami,
- test pilnujący, że `gemini-2.5-flash-lite` nie wrócił do `MODELS`.

Cała istniejąca suite musi zostać zielona.

## Krok 6 — weryfikacja E2E przed wydaniem

Oba fixtures przez żywy łańcuch, porównanie pól ze zdjęciem. **Otwarta pozycja:** na
`receipt_orlen_fiscal.jpg` model zwrócił `receipt_type: "fleet_card"` i datę **2020-07-01**
(drugi fixture z tej samej stacji ma 2026). Nie zweryfikowałem tego na oryginale — w rozdzielczości,
w jakiej widzę ten plik, nie odczytam paragonu. Jeśli to błędny odczyt, poprawka idzie w `PROMPT`
(rozróżnienie formatów), nie w kod, i wtedy dochodzi asercja do testów.

## Krok 7 — wydanie 0.15.0

Wg `feedback_ha_addon_release` / checklisty z pamięci:

1. bump `config.yaml` **i** `fuel_tracker/__init__.py` → `0.15.0`,
2. `grep -riE "freellmapi-|AIza|AQ\.Ab8"` po repo i po diffie — **zero trafień** przed commitem,
3. commit w **inner** `/config/addons/fuel_tracker/.git` (push) + mirror w outer `/config`
   (bez pusha); README: sekcja o skanerze i nowych opcjach,
4. **opublikowany** (nie draft) GH release `v0.15.0` z tabelą opcji i opisem łańcucha,
5. `ha_manage_addon(action="update")` → weryfikacja `version` == `version_latest` == 0.15.0.

## Weryfikacja końcowa (produkcja, po update)

1. Wpisanie kluczy/URL-i/modeli w opcjach add-onu → restart.
2. **Realny skan paragonu** przez UI (`/fillup-form`, Playwright + konsola —
   `feedback_playwright_check_console`), zrzuty do `/config/playwright/`.
3. Log add-onu: `POST /api/receipts/parse` → **200** + wpis, które ogniwo obsłużyło.
4. Test negatywny: celowo zły `gemini_api_key` → UI pokazuje `gemini: HTTP 400 — API key not valid`
   i łańcuch spada na kolejne ogniwo (dowód, że obie naprawy działają end-to-end).
5. Zapis tankowania z prefillowanych pól + podpięty załącznik.

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Router nie obsługuje obrazów | **Zamknięte w Kroku 0 — obsługuje** |
| Konto Google znów wypadnie z free tier | O to chodzi w całym wydaniu — dwa kolejne ogniwa + komunikat mówiący wprost, co się stało |
| Zdjęcie z telefonu większe niż fixture (152 KB) | Gemini przyjmuje `inline_data` do ~20 MB żądania; przy problemie osobna decyzja o zmniejszaniu (Pillow ma wheele musllinux) |
| Wyciek klucza do repo | Klucze wyłącznie w `/data/options.json`; sonda czyta z ENV; grep przed commitem (pkt 7.2) |
| Brak dostępu add-onu do 192.168.0.106:3003 | Router odpowiada z kontenera terminala i z poziomu nokia_tracker (wolna sonda vision to dowód) — sprawdzane ponownie w Kroku 6 na realnym add-onie |

## Uwaga o kluczach

Klucz `AQ.Ab8…` został wklejony w rozmowie, więc jest w transkrypcie sesji. Docelowo oba klucze
żyją wyłącznie w `/data/options.json` add-onu i w config entry llmvision. Jeśli uznasz to za
istotne, możesz go zrotować w AI Studio — nic w kodzie nie zależy od konkretnej wartości.
