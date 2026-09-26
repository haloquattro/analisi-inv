# Verifica della consegna

- Suite offline: 13 test superati (indicatori, dati mancanti, separazione rischio/interesse, filtro massimo 20 candidati, cache, API, watchlist, validazione input, alert senza duplicati e passaggio sicuro del testo a osascript).
- Sintassi JavaScript: verificata con `node --check`.
- Sintassi launcher: verificata con `bash -n`.
- Backend eseguito su macOS con Python 3.12 in ambiente isolato.
- Scansione reale completa: 81 asset osservati, 20 candidati approfonditi, nessun errore riportato.
- Provider reali: ottenuti dettaglio/analisi Apple da Yahoo Finance e Bitcoin da CoinGecko; grafico Apple settimanale con 35 punti.
- Browser: dashboard, pagina Apple, grafico con 1 Settimana selezionata, salvataggio watchlist e selezione confronto verificati. Controllo visivo su viewport stretto.
- Notifica di prova: `osascript` ha restituito successo; la visualizzazione del banner dipende dalle impostazioni del Mac.

La compatibilità Intel è prevista tramite Python universal2 e dipendenze multipiattaforma; questa consegna non è stata eseguita su un secondo Mac Intel. Il doppio clic Finder richiede un Python compatibile nel PATH: il Python di sistema 3.9 rilevato non soddisfa il requisito minimo 3.10. Non sono state modificate le installazioni Python dell’utente.

I dati della verifica sono salvati fuori dalla cartella distribuita. L’archivio non contiene chiavi API, ambienti virtuali, database utente o dati sintetici destinati alla dashboard.

## Aggiornamento 26 settembre 2026

Spiegazioni contestuali dei segnali e glossario espandibile nelle tabelle. Verificati nel browser il testo SMA200 e l’apertura di “Cosa significa?”. Aggiunti commenti esplicativi in italiano nei moduli Python, JavaScript, HTML, CSS e nel launcher. Confronto AST dei moduli Python: logica invariata dopo i commenti. Controlli di sintassi JavaScript e Bash superati; verificati i testi per SMA20/50/200, RSI, volume e ATR.
