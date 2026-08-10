"""Universe builder for PEA Sniper Terminal V-Prime.

Writes ``config/pea_universe.yaml`` from a CURATED, authoritative map of
Euronext Paris tickers (correctness > automation: yfinance search often returns
low-liquidity foreign listings for French blue chips). Every ticker is validated
against Yahoo Finance before being written, and any symbol that no longer returns
price data is dropped and reported.

Run:
    python tools/build_universe.py
"""

import logging
from collections import defaultdict
from pathlib import Path

import yaml
import yfinance as yf

logger = logging.getLogger("build_universe")

_ROOT = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"

# (ticker, display name, sector) - curated Euronext Paris universe.
_CURATED: list[tuple[str, str, str]] = [
    # --- Consumer Cyclical ---
    ("AC.PA", "Accor", "Consumer Cyclical"),
    ("AKW.PA", "Akwel", "Consumer Cyclical"),
    ("ALCAT.PA", "Catana Group", "Consumer Cyclical"),
    ("ALHEX.PA", "Hexaom", "Consumer Cyclical"),
    ("BB.PA", "Bic", "Consumer Cyclical"),
    ("BEN.PA", "Beneteau", "Consumer Cyclical"),
    ("CDA.PA", "Compagnie des Alpes", "Consumer Cyclical"),
    ("CDI.PA", "Christian Dior", "Consumer Cyclical"),
    ("FDJU.PA", "FDJ United", "Consumer Cyclical"),
    ("FNAC.PA", "Fnac Darty", "Consumer Cyclical"),
    ("FR.PA", "Valeo", "Consumer Cyclical"),
    ("FRVIA.PA", "Forvia", "Consumer Cyclical"),
    ("KER.PA", "Kering", "Consumer Cyclical"),
    ("MC.PA", "LVMH", "Consumer Cyclical"),
    ("MMB.PA", "Lagardere", "Consumer Cyclical"),
    ("OPM.PA", "OPmobility", "Consumer Cyclical"),
    ("RMS.PA", "Hermes International", "Consumer Cyclical"),
    ("RNO.PA", "Renault", "Consumer Cyclical"),
    ("STLAP.PA", "Stellantis", "Consumer Cyclical"),
    ("TFF.PA", "TFF Group", "Consumer Cyclical"),
    ("TRI.PA", "Trigano", "Consumer Cyclical"),
    ("VAC.PA", "Pierre et Vacances", "Consumer Cyclical"),
    # --- Consumer Defensive ---
    ("BN.PA", "Danone", "Consumer Defensive"),
    ("BOI.PA", "Boiron", "Consumer Defensive"),
    ("BON.PA", "Bonduelle", "Consumer Defensive"),
    ("CA.PA", "Carrefour", "Consumer Defensive"),
    ("CO.PA", "Casino Guichard", "Consumer Defensive"),
    ("ITP.PA", "Interparfums", "Consumer Defensive"),
    ("LOUP.PA", "LDC", "Consumer Defensive"),
    ("MBWS.PA", "Marie Brizard", "Consumer Defensive"),
    ("OR.PA", "L'Oreal", "Consumer Defensive"),
    ("RCO.PA", "Remy Cointreau", "Consumer Defensive"),
    ("RI.PA", "Pernod Ricard", "Consumer Defensive"),
    ("SAVE.PA", "Savencia", "Consumer Defensive"),
    ("SBT.PA", "Oeneo", "Consumer Defensive"),
    # --- Financial Services ---
    ("ABCA.PA", "ABC Arbitrage", "Financial Services"),
    ("ACA.PA", "Credit Agricole", "Financial Services"),
    ("AMUN.PA", "Amundi", "Financial Services"),
    ("BNP.PA", "BNP Paribas", "Financial Services"),
    ("COFA.PA", "Coface", "Financial Services"),
    ("CS.PA", "AXA", "Financial Services"),
    ("EDEN.PA", "Edenred", "Financial Services"),
    ("ENX.PA", "Euronext", "Financial Services"),
    ("GLE.PA", "Societe Generale", "Financial Services"),
    ("LTA.PA", "Altamir", "Financial Services"),
    ("MF.PA", "Wendel", "Financial Services"),
    ("PEUG.PA", "Peugeot Invest", "Financial Services"),
    ("RF.PA", "Eurazeo", "Financial Services"),
    ("TKO.PA", "Tikehau Capital", "Financial Services"),
    # --- Healthcare ---
    ("AB.PA", "AB Science", "Healthcare"),
    ("ADOC.PA", "Adocia", "Healthcare"),
    ("BIM.PA", "bioMerieux", "Healthcare"),
    ("BLIRD.PA", "Lumibird", "Healthcare"),  # validated below; corrected to LBIRD
    ("CGM.PA", "Cegedim", "Healthcare"),
    ("CLARI.PA", "Clariane", "Healthcare"),
    ("DBV.PA", "DBV Technologies", "Healthcare"),
    ("DIM.PA", "Sartorius Stedim Biotech", "Healthcare"),
    ("EAPI.PA", "EuroAPI", "Healthcare"),
    ("EL.PA", "EssilorLuxottica", "Healthcare"),
    ("EMEIS.PA", "Emeis", "Healthcare"),
    ("ERF.PA", "Eurofins Scientific", "Healthcare"),
    ("GDS.PA", "Ramsay Generale de Sante", "Healthcare"),
    ("GNFT.PA", "Genfit", "Healthcare"),
    ("IPH.PA", "Innate Pharma", "Healthcare"),
    ("IPN.PA", "Ipsen", "Healthcare"),
    ("LNA.PA", "LNA Sante", "Healthcare"),
    ("NANO.PA", "Nanobiotix", "Healthcare"),
    ("OSE.PA", "OSE Immunotherapeutics", "Healthcare"),
    ("SAN.PA", "Sanofi", "Healthcare"),
    ("VETO.PA", "Vetoquinol", "Healthcare"),
    ("VIRP.PA", "Virbac", "Healthcare"),
    ("VLA.PA", "Valneva", "Healthcare"),
    # --- Industrials ---
    ("ADP.PA", "Aeroports de Paris", "Industrials"),
    ("AF.PA", "Air France-KLM", "Industrials"),
    ("AIR.PA", "Airbus", "Industrials"),
    ("ALCIS.PA", "Catering International Services", "Industrials"),
    ("ALEXA.PA", "Exail Technologies", "Industrials"),
    ("ALO.PA", "Alstom", "Industrials"),
    ("AM.PA", "Dassault Aviation", "Industrials"),
    ("ASY.PA", "Assystem", "Industrials"),
    ("AYV.PA", "Ayvens", "Industrials"),
    ("BVI.PA", "Bureau Veritas", "Industrials"),
    ("CEN.PA", "Groupe CRIT", "Industrials"),
    ("CRI.PA", "Chargeurs", "Industrials"),
    ("DG.PA", "Vinci", "Industrials"),
    ("ELIS.PA", "Elis", "Industrials"),
    ("EN.PA", "Bouygues", "Industrials"),
    ("EXE.PA", "Exel Industries", "Industrials"),
    ("FGR.PA", "Eiffage", "Industrials"),
    ("GLO.PA", "GL Events", "Industrials"),
    ("HO.PA", "Thales", "Industrials"),
    ("IDL.PA", "ID Logistics", "Industrials"),
    ("IPS.PA", "Ipsos", "Industrials"),
    ("LR.PA", "Legrand", "Industrials"),
    ("MRN.PA", "Mersen", "Industrials"),
    ("MTU.PA", "Manitou", "Industrials"),
    ("PIG.PA", "Haulotte Group", "Industrials"),
    ("RXL.PA", "Rexel", "Industrials"),
    ("SAF.PA", "Safran", "Industrials"),
    ("SCHP.PA", "Seche Environnement", "Industrials"),
    ("SGO.PA", "Saint-Gobain", "Industrials"),
    ("SPIE.PA", "Spie", "Industrials"),
    ("STF.PA", "STEF", "Industrials"),
    ("SU.PA", "Schneider Electric", "Industrials"),
    ("SW.PA", "Sodexo", "Industrials"),
    ("TEP.PA", "Teleperformance", "Industrials"),
    ("VIE.PA", "Veolia", "Industrials"),
    # --- Technology ---
    ("74SW.PA", "74Software", "Technology"),
    ("ALPRG.PA", "Prologue", "Technology"),
    ("ATE.PA", "Alten", "Technology"),
    ("AUB.PA", "Aubay", "Technology"),
    ("AVT.PA", "Avenir Telecom", "Technology"),
    ("BIG.PA", "Bigben Interactive", "Technology"),
    ("CAP.PA", "Capgemini", "Technology"),
    ("DSY.PA", "Dassault Systemes", "Technology"),
    ("EKI.PA", "Ekinops", "Technology"),
    ("LSS.PA", "Lectra", "Technology"),
    ("NRO.PA", "Neurones", "Technology"),
    ("QDT.PA", "Quadient", "Technology"),
    ("S30.PA", "Solutions 30", "Technology"),
    ("SOI.PA", "Soitec", "Technology"),
    ("SOP.PA", "Sopra Steria", "Technology"),
    ("STMPA.PA", "STMicroelectronics", "Technology"),
    ("SWP.PA", "Sword Group", "Technology"),
    ("VMX.PA", "Verimatrix", "Technology"),
    ("VU.PA", "VusionGroup", "Technology"),
    ("WAVE.PA", "Wavestone", "Technology"),
    ("WLN.PA", "Worldline", "Technology"),
    # --- Communication Services ---
    ("BOL.PA", "Bollore", "Communication Services"),
    ("DEC.PA", "JCDecaux", "Communication Services"),
    ("ETL.PA", "Eutelsat", "Communication Services"),
    ("LOCAL.PA", "Solocal", "Communication Services"),
    ("MMT.PA", "M6 Metropole Television", "Communication Services"),
    ("ODET.PA", "Compagnie de l'Odet", "Communication Services"),
    ("ORA.PA", "Orange", "Communication Services"),
    ("PRC.PA", "Artmarket.com", "Communication Services"),
    ("PUB.PA", "Publicis Groupe", "Communication Services"),
    ("TFI.PA", "TF1", "Communication Services"),
    ("UBI.PA", "Ubisoft", "Communication Services"),
    # --- Basic Materials ---
    ("AI.PA", "Air Liquide", "Basic Materials"),
    ("AKE.PA", "Arkema", "Basic Materials"),
    ("ERA.PA", "Eramet", "Basic Materials"),
    ("JCQ.PA", "Jacquet Metals", "Basic Materials"),
    ("NK.PA", "Imerys", "Basic Materials"),
    ("VCT.PA", "Vicat", "Basic Materials"),
    ("VK.PA", "Vallourec", "Basic Materials"),
    # --- Energy ---
    ("GTT.PA", "GTT", "Energy"),
    ("MAU.PA", "Maurel et Prom", "Energy"),
    ("RUI.PA", "Rubis", "Energy"),
    ("TE.PA", "Technip Energies", "Energy"),
    ("TTE.PA", "TotalEnergies", "Energy"),
    # --- Utilities ---
    ("ENGI.PA", "Engie", "Utilities"),
    ("VLTSA.PA", "Voltalia", "Utilities"),
    # --- Real Estate ---
    ("EIFF.PA", "Societe de la Tour Eiffel", "Real Estate"),
    ("NXI.PA", "Nexity", "Real Estate"),
    # --- ETF sleeve (PEA-eligible; core + broad indices) ---
    ("CW8.PA", "Amundi MSCI World UCITS ETF (Core)", "ETF"),
    ("WPEA.PA", "iShares MSCI World Swap PEA UCITS ETF", "ETF"),
    ("PE500.PA", "Amundi PEA S&P 500 UCITS ETF", "ETF"),
    ("ESE.PA", "BNP Paribas Easy S&P 500 UCITS ETF", "ETF"),
    ("PUST.PA", "Amundi PEA Nasdaq-100 UCITS ETF", "ETF"),
    ("PANX.PA", "Amundi Nasdaq-100 UCITS ETF", "ETF"),
    ("CAC.PA", "Amundi CAC 40 UCITS ETF", "ETF"),
    ("C50.PA", "Amundi Euro Stoxx 50 UCITS ETF", "ETF"),
    ("PCEU.PA", "Amundi PEA MSCI Europe UCITS ETF", "ETF"),
    ("PAEEM.PA", "Amundi PEA Emerging Markets UCITS ETF", "ETF"),
    ("PAASI.PA", "Amundi PEA Asie Emergente UCITS ETF", "ETF"),
    ("PABZ.PA", "Amundi PEA MSCI USA UCITS ETF", "ETF"),
    ("LYPS.DE", "Amundi S&P 500 UCITS ETF", "ETF"),
]

# Corrections applied after a first validation pass (typo -> real symbol).
_FIXUPS = {"BLIRD.PA": "LBIRD.PA", "CGM.PA": "ALCGM.PA"}


def validate(symbols: list[str]) -> set[str]:
    """Return the subset of symbols that return recent price data."""
    good: set[str] = set()
    try:
        data = yf.download(symbols, period="5d", progress=False,
                           auto_adjust=False, group_by="ticker", threads=True)
    except Exception:  # noqa: BLE001
        data = None
    for sym in symbols:
        ok = False
        try:
            lvl0 = data.columns.get_level_values(0) if data is not None else []
            if sym in lvl0 and not data[sym]["Close"].dropna().empty:
                ok = True
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            try:
                hist = yf.Ticker(sym).history(period="5d")
                ok = hist is not None and not hist.empty
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            good.add(sym)
    return good


def main() -> None:
    """Validate the curated list and write the universe YAML."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    rows = [(_FIXUPS.get(t, t), n, s) for t, n, s in _CURATED]
    symbols = [t for t, _, _ in rows]
    logger.info("Validating %d curated tickers...", len(symbols))
    good = validate(symbols)
    dropped = [t for t in symbols if t not in good]
    if dropped:
        logger.warning("Dropped %d invalid tickers (verify manually): %s",
                       len(dropped), ", ".join(dropped))

    buckets: dict[str, list[dict]] = defaultdict(list)
    for ticker, name, sector in rows:
        if ticker in good:
            buckets[sector].append({"ticker": ticker, "name": name})

    payload = {"universe": {k: buckets[k] for k in sorted(buckets)}}
    with open(_UNIVERSE_PATH, "w", encoding="utf-8") as fh:
        fh.write("# PEA Sniper Terminal V-Prime - investable universe\n")
        fh.write("# Curated Euronext Paris tickers, validated against Yahoo "
                 "Finance.\n")
        fh.write("# Regenerate with: python tools/build_universe.py\n\n")
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)

    total = sum(len(v) for v in buckets.values())
    logger.info("Wrote %d tickers across %d sectors to %s",
                total, len(buckets), _UNIVERSE_PATH)


if __name__ == "__main__":
    main()
