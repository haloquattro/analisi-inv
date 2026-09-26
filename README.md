# analisi inv

## 1. Come avviare il progetto

### Requisiti

- **macOS**, su Intel o Apple Silicon.
- **Python 3.10 o successivo**, disponibile su [python.org/macOS](https://www.python.org/downloads/macos/). L’installer universal2 supporta entrambe le architetture. Su questo Mac è già presente Python 3.12.
- Internet per installare le dipendenze, recuperare i dati e caricare Chart.js/Tailwind dalle CDN.
- Accesso autorizzato al repository GitHub privato per scaricare i sorgenti.

### Avvio con doppio clic

1. Scarica il repository tramite **Code → Download ZIP** ed estrailo, oppure clonalo:

   ```bash
   git clone https://github.com/haloquattro/analisi-inv.git
   cd analisi-inv
   ```

   Per il repository privato serve l’autenticazione GitHub, ad esempio tramite un gestore di credenziali o SSH. Non inserire token nel codice o nell’URL del repository.

2. Nel Terminale, dalla cartella del progetto, abilita il launcher:

   ```bash
   chmod +x run_mac.command
   ```

3. Fai doppio clic su **`run_mac.command`** nel Finder, oppure esegui:

   ```bash
   ./run_mac.command
   ```

4. Se il browser non si apre automaticamente, visita **[http://localhost:8000](http://localhost:8000)**.

Il launcher cerca Python compatibile anche quando il comando predefinito punta al vecchio 3.9, crea `.venv`, installa i requisiti e avvia FastAPI. Il primo avvio può richiedere alcuni minuti; quelli successivi riutilizzano le dipendenze installate.

**Lascia aperto il Terminale.** Premi **Ctrl+C** per fermare il server. Chiudere il browser non arresta l’app. Non viene installato un servizio all’accensione del Mac.

La dashboard legge subito i risultati salvati. Al primo avvio bisogna attendere la scansione in background prima di vedere le opportunità.

### Configurazione facoltativa

```bash
cp .env.example .env
```

Apri `.env` con un editor e configura, se disponibile, una chiave **Demo** di [CoinGecko](https://www.coingecko.com/en/api):

```ini
COINGECKO_API_KEY=inserisci_la_tua_chiave_demo
SCAN_INTERVAL_SECONDS=3600
```

Non pubblicare `.env`: è escluso da Git. Senza chiave l’app tenta l’accesso pubblico; disponibilità e quote dipendono dal provider. Usa valori senza virgolette, espansioni shell o spazi intorno a `=`.

### Avvio manuale e test

Usa Python compatibile; sostituisci `python3` con `python3.12` se necessario:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Il comando manuale non legge `.env`: esporta le variabili nella shell o usa il launcher. Avvia **un solo worker Uvicorn**, perché scheduler e limiti delle richieste sono gestiti nel processo.

## 2. Cosa fa, in breve

**analisi inv** osserva azioni, criptovalute e oro e seleziona automaticamente segnali tecnici da approfondire. Mostra interesse e rischio separati, con spiegazioni in italiano delle ragioni favorevoli, dei fattori negativi e dei dati mancanti.

Include dashboard con filtri, ricerca per nome/ticker, grafici interattivi, watchlist, storico delle analisi, notifiche native macOS e confronto di due o tre asset. Supporta modalità chiara/scura e un glossario “Cosa significa?” per indicatori e fondamentali.

Non esegue ordini, non richiede un conto di trading e non promette rendimenti. I punteggi descrivono regole tecniche, non probabilità di guadagno.

## 3. Architettura e struttura del codice

| Componente | Tecnologia | Responsabilità |
| --- | --- | --- |
| Backend locale | Python / FastAPI | API, scheduler, stato delle analisi e gestione degli errori |
| Azioni e oro | yfinance | Prezzi Yahoo, storico, volumi e fondamentali |
| Criptovalute | CoinGecko API | Paniere per capitalizzazione, quotazioni e storico |
| Persistenza | SQLite / SQLAlchemy | Watchlist, cache, storico e alert |
| Interfaccia | HTML, CSS, JavaScript, Tailwind CDN | Pagine responsive, filtri, spiegazioni e temi |
| Grafici | Chart.js via CDN | Prezzi interattivi e tooltip con data/ora |
| Notifiche | `osascript` | Avvisi nativi nel Centro Notifiche di macOS |

```text
analisi-inv/
├── backend/
│   ├── main.py                 # API, scheduler e transizioni degli alert
│   ├── models.py               # Tabelle SQLite e sessioni SQLAlchemy
│   └── services/
│       ├── data_fetcher.py     # Provider, cache, rate limit e imbuto
│       ├── analyzer.py         # Indicatori, punteggio e fattori esplicativi
│       └── notifier.py         # Integrazione sicura con AppleScript
├── frontend/
│   ├── index.html              # Struttura della pagina
│   ├── style.css               # Temi e layout responsive
│   └── app.js                  # Navigazione, grafici e glossario
├── tests/test_radar.py         # Test offline del motore e delle API
├── run_mac.command            # Avvio dal Finder
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

I commenti nel codice spiegano formule, dati mancanti, cache, concorrenza, transizioni delle notifiche e gestione delle risposte asincrone. Il CSS locale mantiene leggibile l’interfaccia anche se Tailwind non si carica; il grafico richiede Chart.js.

## 4. Selezione degli asset: l’imbuto

### Universo

- **50 azioni large cap USA fisse e modificabili**: non è una classifica live certificata dei primi 50 titoli dell’S&P 500.
- **30 crypto**, selezionate da CoinGecko per capitalizzazione; possono includere stablecoin o token wrapped.
- **Oro `GC=F`**, future continuo COMEX, non prezzo spot né acquisto di oro fisico.

Puoi creare `universe.json` nella radice con un array JSON di **esattamente 50 ticker Yahoo distinti**, quindi riavviare l’app. Non vengono scaricate le capitalizzazioni di tutti i componenti dell’S&P 500 a ogni scansione.

### Procedura

1. Yahoo scarica in batch tre mesi di barre giornaliere per azioni e oro. CoinGecko restituisce le prime 30 crypto in una richiesta.
2. Azioni/oro sono candidati con variazione assoluta **≥2%** oppure volume **≥1,5×** la media delle 20 sedute precedenti.
3. Per crypto: variazione assoluta **≥3%** oppure volume 24h/capitalizzazione **≥20%**. Questo rapporto è un turnover, non un confronto con i volumi storici.
4. I candidati vengono ordinati per il massimo tra i rapporti dei segnali e delle rispettive soglie. Si approfondiscono **al massimo 20 asset**, senza riempire artificialmente la lista.
5. La Home pubblica i candidati con interesse **Elevato** o **Moderato**.

Watchlist, alert e dettagli richiesti dall’utente sono eccezioni esplicite all’imbuto. Una watchlist molto grande può aumentare tempi e richieste API.

## 5. Indicatori e significato dei segnali

| Indicatore | Calcolo | Interpretazione |
| --- | --- | --- |
| SMA20/50/200 | Media semplice delle ultime 20, 50 o 200 chiusure valide | Riferimento per trend breve, medio e lungo |
| RSI14 | Rapporto tra guadagni e perdite smussati con Wilder, su scala 0–100 | Forza dei movimenti recenti, detta momentum |
| ATR14 | Wilder del true range: massimo tra high−low, distanza high/chiusura precedente e low/chiusura precedente | Ampiezza delle oscillazioni, non direzione |
| Bollinger | SMA20 ± due deviazioni standard dei prezzi (`ddof=0`) | Fascia descrittiva, non limiti garantiti |
| Volume relativo | Ultimo volume / media dei 20 precedenti | 1,5 significa scambi del 50% superiori alla media |
| Volatilità annualizzata | Deviazione standard campionaria degli ultimi 20 rendimenti semplici × √252, oppure √365 per crypto | Variabilità recente riportata su scala annua |

Wilder parte dalla media semplice dei primi 14 valori e poi aggiorna con peso 1/14. RSI di una serie piatta = 50; in presenza di rialzi ma nessuna perdita = 100.

“Prezzo sopra SMA200” significa che l’ultima chiusura usata nell’analisi è sopra la media delle ultime **200 chiusure giornaliere**. Può coesistere con “Prezzo sotto SMA20”: i periodi sono diversi. Nessuna delle due condizioni garantisce la direzione futura del prezzo.

“Volume confrontabile non disponibile” e “ATR non disponibile” indicano limiti dei dati, non volume zero o rischio automaticamente elevato. Per le crypto CoinGecko restituisce campioni, non vere barre OHLC: massimi e minimi non vengono inventati per calcolare ATR.

Ogni segnale ha una spiegazione sotto il testo. Nelle tabelle **Cosa significa?** apre una definizione, utilizzabile anche da tastiera o touch.

### Osservazioni e valutazioni

Prezzo, volume, P/E, EPS, capitalizzazione e dividendi provengono dai provider. Indicatori e punteggio sono calcoli dell’app. I fondamentali sono informativi e **non entrano nel punteggio tecnico**.

## 6. Interesse e rischio: due valutazioni diverse

### Punteggio di interesse

Si parte da 50 punti:

| Condizione | Contributo |
| --- | --- |
| Prezzo sopra / non sopra SMA20 | +10 / −10 |
| Prezzo sopra / non sopra SMA50 | +15 / −15 |
| Prezzo sopra / non sopra SMA200 | +20 / −20 |
| RSI tra 45 e 65 inclusi | +10 |
| RSI >70 | −15 |
| RSI <30 | −10 |
| Volume relativo ≥1,5 con prezzo in rialzo / non in rialzo | +10 / −10 |

Negli altri casi RSI/volume non aggiungono contributi. Il risultato è limitato a 0–100. La comparazione delle SMA usa `>`: in caso di uguaglianza si applica il ramo non sopra, descritto dal testo sintetico come “sotto”.

| Interesse | Punteggio |
| --- | --- |
| Elevato | ≥75 |
| Moderato | 60–74 |
| Neutrale | 35–59 |
| Attenzione | <35 |
| Dati Insufficienti | Meno di 200 chiusure valide o impossibilità di calcolo |

Con meno di 200 chiusure non viene assegnato un punteggio, ma alcuni indicatori possono essere disponibili. ATR e volume relativo mancanti sono segnalati senza fabbricare valori.

### Rischio

È classificato separatamente usando la volatilità annualizzata:

- **Basso:** <20%.
- **Moderato:** ≥20% e <45%.
- **Elevato:** ≥45%.
- **Dati Insufficienti:** volatilità non calcolabile.

Interesse Elevato può coesistere con rischio Elevato. Il modello non misura tutti i rischi: credito, liquidità, eventi estremi, perdita dell’ancoraggio di stablecoin e rischi operativi non sono compresi.

## 7. Cache, aggiornamenti e qualità dei dati

La scansione parte all’avvio e viene ripetuta dopo **3.600 secondi dalla conclusione** della precedente. Il minimo configurabile è 900. La scansione manuale è limitata a una ogni 15 minuti e il lock evita sovrapposizioni.

| Dati | Cache |
| --- | --- |
| Scansione primaria, quotazioni e grafici brevi | 15 minuti |
| Storico lungo per l’analisi e grafico crypto annuale | 6 ore |
| Fondamentali e ricerca | 24 ore |

Le richieste CoinGecko sono distanziate di almeno **6,1 secondi**. Dopo HTTP 429 il servizio attende almeno 120 secondi, estendendo la pausa in base a `Retry-After` quando disponibile. Controlla anche il budget mensile del piano; nessuna frequenza garantisce il rispetto di qualsiasi quota.

Le metriche mancanti restano `null` e vengono mostrate come **Dati insufficienti**. Se un aggiornamento fallisce, il dettaglio può mostrare dati precedenti esplicitamente marcati. L’interfaccia distingue orario della barra, recupero e data degli indicatori; segnala risultati salvati oltre 90 minuti come non recenti.

- Azioni/oro: variazione fra le ultime due chiusure disponibili; l’ultima barra può essere ancora in corso.
- Crypto: variazione 24h comunicata dal provider.
- Indicatori: barre precedenti al giorno UTC corrente, per evitare barre parziali. Questo introduce ritardo anche dopo la chiusura odierna.
- Yahoo: prezzi non aggiustati; split/dividendi possono produrre discontinuità.
- GC=F: il passaggio da un contratto all’altro può alterare la serie.

## 8. Watchlist, alert e confronto

Il pulsante ☆ salva/rimuove un asset dalla watchlist. Gli asset seguiti vengono aggiornati a ogni scansione. Lo storico registra cambi di analisi o data di riferimento, non ogni visita; conserva fino a 200 record per asset e ne mostra fino a 30.

### Notifiche macOS

Dal dettaglio puoi attivare cambio di interesse e/o soglia percentuale assoluta. Nella sezione Alert è presente la notifica di prova.

- Il salvataggio usa lo stato corrente come riferimento: nessuna notifica immediata per una soglia già superata.
- I cambi d’interesse confrontano classificazioni valide; i dati insufficienti non generano falsi cambi.
- La soglia usa `abs(variazione) >= soglia`; dopo l’avviso si riarma solo al rientro sotto soglia.
- Stato persistente in SQLite; se `osascript` fallisce, la transizione non viene consumata.
- Il successo di `osascript` non garantisce un banner visibile: verifica Notifiche e Full immersion nelle impostazioni macOS.

Il Mac deve essere sveglio e il server attivo. Non sono rilevati necessariamente tutti gli attraversamenti fra due scansioni; nessun controllo durante lo stop.

### Confronto

Seleziona due o tre asset con **Confronta**. La tabella affianca prezzi, variazioni, interesse, rischio, volatilità e punteggio. Il colore evidenzia differenze, non il “migliore investimento”. Valute e unità di prezzo diverse non sono direttamente confrontabili.

## 9. Persistenza, privacy e manutenzione

Il database predefinito è **`data/radar.sqlite3`**: contiene cache, analisi, watchlist, storico e alert. Conserva `data` durante aggiornamenti e rinomina. Per un percorso alternativo imposta in `.env`:

```ini
RADAR_DATA_DIR=/Users/tuo-nome/Library/Application Support/AnalisiInv
```

Le variabili `RADAR_*`, le chiavi delle preferenze nel browser e il nome `radar.sqlite3` sono mantenuti per compatibilità; non richiedono migrazione. Il nome visibile è **analisi inv**; repository e cartella si chiamano **analisi-inv**.

Per un backup arresta il server e copia l’intera cartella dati. SQLite usa WAL, quindi durante l’esecuzione possono esserci file aggiuntivi accanto al database.

Git esclude `.env`, database, log, `.venv`, cache e archivi generati. Il repository contiene soltanto sorgenti, test, documentazione e il modello di configurazione privo di segreti. La visibilità privata non sostituisce queste esclusioni.

Il backend ascolta solo su `127.0.0.1`. Le scritture API richiedono `X-Radar-Client: local` e un Origin coerente, quando presente. Non c’è autenticazione per uso remoto: non esporre direttamente il server su Internet.

## 10. API, test e risoluzione dei problemi

La documentazione interattiva è su **[localhost:8000/docs](http://localhost:8000/docs)**:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/status
curl -X POST -H 'X-Radar-Client: local' http://localhost:8000/api/scan
```

Endpoint principali: `/api/opportunities`, `/api/search`, `/api/asset/{id}`, `/api/watchlist`, `/api/alerts` e `/api/compare`. Gli identificatori includono il tipo/provider, ad esempio `stock:AAPL`, `crypto:bitcoin`, `gold:GC=F`.

La suite offline verifica indicatori, dati mancanti, cache, limite dei candidati, API, watchlist, input, alert senza duplicati e testo passato in sicurezza a `osascript`. Usa database temporanei e dati sintetici solo nei test. [VERIFICA.md](VERIFICA.md) documenta le verifiche eseguite: non garantisce la disponibilità futura delle fonti.

| Problema | Soluzione |
| --- | --- |
| Python vecchio | Installa Python 3.10+; il launcher cerca anche Homebrew/python.org |
| Nessuna opportunità | Distingui assenza di segnali, scansione in corso e fonti incomplete |
| CoinGecko non risponde | Verifica rete, chiave Demo e quote; attendi dopo un rate limit |
| Porta 8000 occupata | Ferma l’altra applicazione o la precedente istanza con Ctrl+C |
| Browser non si apre | Apri manualmente http://localhost:8000 |
| Grafico assente | Controlla CDN Chart.js e disponibilità dello storico |
| Notifiche assenti | Usa il test; verifica permessi, Full immersion e server acceso |
| Venv spostato o cambio architettura | Ricrea `.venv` con Python compatibile, conservando `.env` e `data` |

## 11. Limiti e fonti

Le regole sono euristiche: non è incluso un backtest e il punteggio non è una probabilità. Dati ritardati, corporate action, stablecoin, rollover e copertura incompleta possono rendere alcuni segnali poco significativi. L’app supporta osservazione e studio, non consulenza finanziaria personalizzata.

- [yfinance: documentazione](https://ranaroussi.github.io/yfinance/reference/index.html)
- [CoinGecko: documentazione](https://docs.coingecko.com/)
- [CoinGecko: mercati e capitalizzazione](https://docs.coingecko.com/reference/coins-markets)

Consulta le condizioni delle fonti prima di riutilizzare o ridistribuire dati. La compatibilità Intel è prevista dalle tecnologie impiegate; i test di esecuzione sono stati effettuati sul Mac locale, non su un secondo Mac Intel.
