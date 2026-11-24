# SwingPulse — Daily US Stock Screener & Swing Trading Platform

Lokální, snadno rozšiřitelná webová aplikace pro denní swing-trading a poziční screening US akcií (UI kompletně v angličtině), navržená pro vývoj na **Windows 11** a jednoprocestový produkční provoz na **Raspberry Pi (ARM64 Linux)**.

---

## Rychlé spuštění jedním příkazem

### Windows (vývojový režim s hot-reloadem)
```powershell
.\start.ps1
```
*(Nebo stačí dvakrát kliknout na [`start.bat`](start.bat) v kořenové složce.)*

Skript automaticky:
1. Při prvním spuštění vytvoří `backend/.venv`, nainstaluje Python knihovny a `frontend/node_modules`.
2. Spustí **FastAPI backend** (`http://127.0.0.1:8000`) i **Vite frontend** (`http://localhost:5173`).
3. Otevře aplikaci v prohlížeči (pro spuštění bez otevření prohlížeče použij `.\start.ps1 -NoBrowser`).

### Windows (jednoprocestový produkční režim)
```powershell
.\start.ps1 -Prod
```
Servíruje API i zkompilovaný frontend (`frontend/dist`) z jediného procesu na `http://localhost:8000`.

### Raspberry Pi / Linux
```bash
bash start.sh
```
Spustí jediný proces na portu `8000` (`http://<ip-maliny>:8000`, bez nutnosti běžícího Node.js nebo Dockeru).

---

## Požadavky (Prerequisites)

- **Windows vývoj:** Python **3.12+**, Node.js **20+**
- **Raspberry Pi produkce:** Python **3.12+** (všechny balíčky v `backend/requirements.txt` mají čisté `aarch64` wheels — bez `TA-Lib`). Pokud na RPi není Node.js, stačí na Windows spustit `.\scripts\build-prod.ps1` a zkopírovat složku `frontend/dist/`.

---

## Jak aplikaci používat a nastavit

### 1. První sken po spuštění
1. Otevři `http://localhost:5173` (dev) nebo `http://localhost:8000` (prod).
2. Vpravo nahoře klikni na **Run Scan**:
   - **Quick Scan (~25 s):** ~45–70 nejlikvidnějších US lídrů, denní pohyby + všechny tvé watchlisty.
   - **Full Universe Scan (~600 tickerů):** Kompletní S&P 500 + Nasdaq 100 + Finviz/Yahoo movers + OpenInsider cluster buys (Fáze 1 spočítá techniku pro ~600 akcií, Fáze 2 dotáhne fundamenty, 1H data, earnings a opční strategie pro Top 90 + tvé watchlisty).
   - **Refresh Watchlists Only:** Osvěží jen akcie v tvých seznamech.
3. Kdykoliv můžeš nahoře do pole **Analyze ticker** zadat libovolný US ticker (např. `PLTR`) a stisknout Enter.

### 2. Žebříček, krátkodobé (Long/Short) vs. střednědobé pohledy a Watchlisty
- **Presety (`Preset`):**
  - *Short-Term High R:R (Long/Short)* — hledá vstupy blízko S/R a SMC zón s vyšším potenciálem zisku než ztráty (`R:R`) a shodou s trendem indexu (`SPY`/`QQQ`/`IWM`).
  - *Medium-Term Investing* — preferuje finanční zdraví firmy, relativní sílu, dlouhodobé kanály, SMC Discount zóny a nákupy insiderů.
  - *All-Around Swing*, *Channel Bounce*, *Breakout & Momentum*, *Options Income (High IV)*, *Insider & Quality*.
- **Řazení (`Sort`):** Přepínač pro seřazení podle skóre (`Preset Score`), `Best R:R & Index Trend`, `Max Potential Gain %` (zobrazí i kvalitní **Short** příležitosti) nebo `Tightest S/R Stop Risk`.
- **Vlastní listy (`Watchlists`):** Záložka vlevo nahoře umožňuje tvořit neomezené seznamy označené horizontem (`Short-Term`, `Medium-Term`, `General`). Akcii přidáš do jednoho či více listů na 1 klik (nebo klávesou `W`). Mezi akciemi se pohybuješ šipkami `↑` / `↓`.

### 3. Graf, Smart Money zóny a vysvětlení skóre
- **Časové rámce:** `1W` (týdenní) | `1D` (denní) | `1H` (hodinový).
- **Vrstvy v grafu:**
  - `SMC Zones` — Order Blocky (Demand/Supply), Fair Value Gaps (`FVG`), `BOS`/`CHoCH` a `Premium`/`Discount` zóny (inspirováno `pine/smart money 1.pine`).
  - `Channels` — lineární trendové kanály (40/75/120d) a upozornění na dotek spodní/horní hrany nebo průraz.
  - `S/R Zones`, `Price Action` (Hammer, Shooting Star, Engulfing, Break & Retest), `Trade Plan` (Entry/SL/TP1/TP2 box), `EMAs` (20/50/200), `Vol POC` (Volume Profile Point of Control), `Volume`.
- **Živá cena a rozpad skóre:** Kliknutím na libovolnou akcii se dotáhne živá cena (`LIVE • IBKR` nebo `Yahoo Live`). Karta *Score Breakdown & "Why Today?"* zobrazuje normalizovanou váhu (`w %`) a přesný bodový příspěvek (`+X.X`) každého modulu k celkovému skóre `0–100`.

### 4. Nastavení (`Settings` ozubené kolečko vpravo nahoře)
- **Plugins & Presets:** Zapínání/vypínání libovolného pluginu a úprava vah presetu.
- **Account, IBKR & Data:**
  - **Account Equity & Risk %:** Výchozí parametry pro Position Sizing kalkulačku.
  - **IBKR Gateway (Read-Only):** Zadej `Host` (lokálně `127.0.0.1` nebo LAN IP počítače s Gateway), `Port` (`4002` pro Paper účet, `4001` pro Live) a `Client ID`, pak klikni na **Test IBKR Connection**. Pokud Gateway neběží, aplikace automaticky používá Yahoo Finance + analytický Black-Scholes výpočet Greeks. *(Aplikace je striktně read-only a nikdy nezadává obchodní příkazy.)*
  - **Historical Depth & Background Backfill:**
    - *Development Mode (Light):* `2Y` denní, `120D` 1H, `5Y` týdenní data (backfill jen ručně tlačítkem *Run Backfill Batch Now*).
    - *Production / Raspberry Pi (Deep):* `5Y` denní, `1Y` 1H, `10Y` týdenní data + automatické průběžné dotahování historie firem v databázi na pozadí (přes IBKR Gateway s fallbackem na Yahoo).
  - **Finnhub API Key (volitelný):** Pro firemní zprávy a konsenzus analytiků zdarma.
- **Schedule & Alerts:** Časy automatického ranního (pre-market) a večerního (post-market) skenu + volitelné **Telegram** / **Discord** notifikace.

---

## Nasazení na Raspberry Pi (`akcie.dantm.info`)

Aplikace běží na Raspberry Pi (`ssh pi5`) pod systemd službou `stock-screener` na portu `8085` (kód v `/home/pi/src/stock-screener`, veškerá akciová data a SQLite databáze + cache na velkém disku `/media/pi/Seagate8gb/tickers`), vystavená přes existující Cloudflare Tunnel na **`https://akcie.dantm.info`**.

### Rychlý redeploy z libovolného vývojového PC (přes `ssh pi5`)

- **Windows (PowerShell):**
  ```powershell
  .\deploy.ps1
  ```
- **Linux / macOS:**
  ```bash
  bash deploy.sh
  ```

Skript překompiluje frontend (`frontend/dist`), zabalí aplikaci (bez `.venv`, `node_modules` a lokálních dočasných dat), nahraje přes `scp` na `pi5`, při změně `backend/requirements.txt` doinstaluje Python balíčky, aktualizuje `/etc/systemd/system/stock-screener.service` (auto-start po restartu včetně čekání na `/media/pi/Seagate8gb`) a ověří zdraví serveru.

Volitelné přepínače:
- `-SyncDb` / `--sync-db` — nahraje i aktuální lokální databázi `backend/data/screener.db` na `/media/pi/Seagate8gb/tickers/screener.db` a aktivuje `production` profil historie. *(Při úplně prvním deployi se DB nahraje automaticky, pokud na disku ještě neexistuje.)*
- `-DataDir` / `--data-dir` — cílová složka pro databázi a cache na malině (výchozí `/media/pi/Seagate8gb/tickers`).
- `-SkipBuild` / `--skip-build` — přeskočí `npm run build` a použije stávající `frontend/dist`.

---

## Ruční instalace na Raspberry Pi (`systemd`)

1. Na Windows (nebo přímo na RPi, pokud má Node) připrav produkční build frontendu:
   ```powershell
   .\scripts\build-prod.ps1
   ```
2. Na Raspberry Pi spusť instalační skript:
   ```bash
   bash scripts/setup-rpi.sh
   ```
3. Nainstaluj službu ze šablony [`deploy/stock-screener.service`](deploy/stock-screener.service) (přesné příkazy vypíše `setup-rpi.sh`):
   ```bash
   sudo systemctl enable --now stock-screener
   ```

---

## Testy a rozšiřování o vlastní pluginy

- Spuštění testů:
  ```powershell
  backend\.venv\Scripts\python.exe -m pytest -v tests
  ```
- Přidání nového analyzátoru: vytvoř jeden `.py` soubor v `backend/app/plugins/analyzers/` dědící z `AnalyzerPlugin`. Po restartu se automaticky zaregistruje do žebříčku, nastavení vah i grafu (detaily viz [`AGENTS.md`](AGENTS.md)).
