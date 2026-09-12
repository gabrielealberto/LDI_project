# LDI cash-flow matching

Workflow Python per costruire un portafoglio obbligazionario che copra una
schedule di passività future mensili. Il progetto scarica dati ufficiali e di
mercato, li valida, costruisce i cash flow, risolve un problema MILP e produce
Excel, audit Parquet e grafici.

Il progetto è pensato per un’esecuzione batch schedulata. `main.py` resta
compatibile come entry point interattivo; per l’esecuzione operativa usare
`scripts/run_ldi.py`.

## Architettura

```text
Scheduler
    |
    v
scripts/run_ldi.py
    |
    +-- lock esclusivo anti-concorrenza
    +-- manifest di esecuzione
    +-- main.py
           |
           +-- ingestion dati esterni
           |     +-- bond market data
           |     +-- curva ECB Svensson
           |     +-- FOI ISTAT
           |     +-- HICP Eurostat
           |
           +-- validazione e pulizia
           +-- cash flow generation
           +-- MILP cash-flow matching
           +-- stress test, Excel e grafici
```

I downloader condividono primitive operative in
`core/ingestion_support.py`:

- retry HTTP con exponential backoff per errori transitori;
- timeout espliciti;
- scrittura Parquet atomica tramite file temporaneo e replace;
- nessuna pubblicazione di file parziali.

Il lock viene scritto in `data/processed/.ldi-run.lock`. Il manifest dell’ultima
esecuzione è `data/processed/run_manifest.json` e contiene stato, timestamp,
host, processo, durata, stage completati ed eventuale errore.

## Installazione

Usare Python 3.11 o superiore in un ambiente virtuale:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## Esecuzione operativa

Da PowerShell, dalla root della repo:

```powershell
python scripts/run_ldi.py
```

Il comando:

1. impedisce due esecuzioni contemporanee;
2. riusa la snapshot bond archiviata oggi oppure scarica una nuova coppia di file;
3. usa la cache FOI/HICP se valida, mensile, gap-free, positiva e non più
   vecchia di due mesi;
4. aggiorna gli indici mancanti o non validi;
5. ricostruisce tutti gli artefatti derivati;
6. produce il report e aggiorna il manifest.

Per uso interattivo è ancora possibile eseguire:

```powershell
python main.py
```

Questa modalità non aggiunge il lock e il manifest; per un job schedulato usare
sempre `scripts/run_ldi.py`.

## Schedulazione consigliata

Su Windows usare Task Scheduler con:

- programma: percorso assoluto del Python nel virtual environment;
- argomenti: `scripts/run_ldi.py`;
- directory di avvio: root assoluta della repo;
- frequenza: giornaliera nei giorni lavorativi, preferibilmente dopo la
  disponibilità dei dati ufficiali;
- esecuzione con un account tecnico dedicato;
- logging stdout/stderr verso un file gestito dal sistema operativo;
- alert se il processo restituisce exit code diverso da zero.

Esempio:

```text
Programma: C:\path\to\LDI\.venv\Scripts\python.exe
Argomenti: C:\path\to\LDI\scripts\run_ldi.py
Avvia in: C:\path\to\LDI
```

Su Linux o container usare lo stesso comando tramite cron, systemd timer o un
orchestratore come Prefect/Dagster. Il codice di ingestion è già idempotente e
può essere spostato in un job containerizzato senza cambiare il dominio LDI.

## Dati e fonti

| Dataset | Fonte | Destinazione |
|---|---|---|
| Bond market | SimpleTools for Investors | `data/raw/bonds/fd_YYYYMMDD.parquet`, `bi_YYYYMMDD.parquet` |
| Curva Svensson | ECB Data API | `data/raw/curves/yc_YYYYMMDD.parquet` |
| FOI escluso tabacchi | ISTAT SDMX + Rivaluta | `data/foi_xt_it.parquet` |
| HICP escluso tabacchi | Eurostat | `data/hicp_xt_ea.parquet` |

I dati esterni vengono validati prima di essere pubblicati. Le fonti FOI e HICP
vengono anche controllate per positività, date mensili e continuità temporale.
L’endpoint storico ISTAT viene parametrizzato sul mese completo precedente, non
su una data hardcoded.

### Archivio storico bond

Ogni download bond genera una coppia immutabile di Parquet nella directory
`data/raw/bonds/`:

```text
fd_YYYYMMDD.parquet    # dati End of Day
bi_YYYYMMDD.parquet    # elenco/anagrafica obbligazioni
```

`YYYYMMDD` è la data di archiviazione, non una data dedotta dal contenuto del
mercato. Durante una nuova esecuzione la pipeline controlla prima la coppia del
giorno: se è presente e passa la validazione di schema, viene riusata senza
chiamare il sito. In assenza della coppia viene eseguito il download e vengono
creati soltanto nuovi file, mai sovrascritti snapshot storiche.

Se il download fallisce, la pipeline può usare l’ultima coppia completa entro
cinque giorni. Il fallback viene scritto nei log; oltre la soglia il job fallisce
in modo esplicito, evitando di costruire un portafoglio con dati troppo vecchi.

### Archivio storico curve ECB

Le curve Svensson seguono la stessa policy in `data/raw/curves/`:

```text
yc_YYYYMMDD.parquet
```

La pipeline riusa la curva archiviata oggi se è valida; altrimenti ne scarica una
nuova. In caso di errore dell’ECB Data API può usare l’ultima curva valida entro
cinque giorni, altrimenti interrompe il job. Le utility di valutazione leggono
automaticamente la snapshot curva più recente e valida, con compatibilità per il
vecchio file `data/raw/ecb_svensson.parquet` finché presente.

## Policy delle liabilities

Le date configurate in `data/config/liabilities.json` seguono una policy
esplicita e inclusiva: `end_date` identifica l'ultima annualita da pagare.
Il parametro `LIABILITY_PAYMENT_TIMING` in `core/utils.py` puo essere
`period_start` (inizio periodo) o `period_end` (fine periodo). Le date
contrattuali vengono usate per l'indicizzazione FOI; il matching resta
aggregato al mese del pagamento.

## Output

Gli artefatti generati sono locali e non devono essere committati:

- `data/processed/ldi_optimization.xlsx` — report Excel;
- `data/processed/bond_cashflows.parquet` — cash flow dettagliati;
- `data/processed/bond_cashflow_matrix.parquet` — matrice mensile;
- `data/processed/inflation_baseline.parquet` — baseline FOI/HICP;
- `data/processed/inflation_stress_*.parquet` — audit degli stress;
- `data/processed/plots/` — grafici;
- `data/processed/run_manifest.json` — esito dell’ultima esecuzione.

I file temporanei e i dati grezzi restano esclusi da Git tramite `.gitignore`.

## Riproducibilità dei run

Ogni esecuzione operativa riceve un `run_id`. Il manifest corrente è scritto in
`data/processed/run_manifest.json`; una copia storica immutabile viene salvata
in `data/processed/run_manifests/`. Il manifest registra hash SHA-256 e metadati
degli input e degli output, snapshot effettivamente utilizzate, configurazioni
JSON, commit Git, versione Python, fallback, warning e risultati quantitativi
del solver. Gli snapshot raw archiviati e gli hash permettono di ricostruire un
risultato senza affidarsi ai file derivati eventualmente sovrascritti dal run
successivo.

## Qualità e CI

Controlli locali:

```powershell
python -m ruff format . --check
python -m ruff check .
python -m unittest discover -s tests -v
```

La CI GitHub esegue gli stessi controlli in un ambiente pulito. I test sono
deterministici e non richiedono accesso alla rete; l’esecuzione completa di
`scripts/run_ldi.py` richiede invece rete e fonti ufficiali disponibili.

## Operatività e incidenti

Se il job fallisce:

1. controllare `data/processed/run_manifest.json`;
2. verificare il log del Task Scheduler;
3. controllare che `.ldi-run.lock` non appartenga a un processo ancora attivo;
4. rimuovere il lock solo dopo aver verificato che sia stale;
5. rieseguire il job.

Un fallimento non sostituisce i dataset validi precedenti: la scrittura atomica
mantiene l’ultimo output completo disponibile.

## Struttura principale

```text
core/                         dominio, pipeline e orchestrazione
scripts/downloaders/          acquisizione fonti esterne
scripts/cleaners/             pulizia universo obbligazionario
scripts/run_ldi.py            entry point per scheduler
data/config/                  configurazione contrattuale versionata
data/raw/                     input raw locali, non versionati
data/processed/               output derivati, non versionati
tests/                        test unitari e contrattuali
```

Il progetto non è un servizio web: è un batch finanziario con output auditabili.
Prima dell’uso produttivo vanno configurati account tecnico, directory assolute,
backup degli artefatti e un canale di alerting operativo.
