import codecs

# 1. Update Market Prices API
with codecs.open('00_data_sensors/market_prices_api.py', 'r', encoding='utf-8') as f:
    text = f.read().replace('\r\n', '\n')

old_yf = """            try:
                raw = yf.download(
                    remaining_tickers,
                    start=start_date,
                    progress=False,
                    auto_adjust=True,
                    group_by="column",
                    threads=True,
                )
                if raw is not None and not raw.empty:
                    yf_df = self._flatten(raw, remaining_tickers)
            except"""

new_yf = """            try:
                import time
                import pandas as pd
                chunk_size = 40
                all_yf_dfs = []
                for i in range(0, len(remaining_tickers), chunk_size):
                    chunk = remaining_tickers[i:i + chunk_size]
                    raw = yf.download(
                        chunk,
                        start=start_date,
                        progress=False,
                        auto_adjust=True,
                        group_by="column",
                        threads=True,
                    )
                    if raw is not None and not raw.empty:
                        flat_chunk = self._flatten(raw, chunk)
                        all_yf_dfs.append(flat_chunk)
                    
                    if i + chunk_size < len(remaining_tickers):
                        time.sleep(2)
                
                if all_yf_dfs:
                    yf_df = pd.concat(all_yf_dfs, ignore_index=True)
            except"""

text = text.replace(old_yf, new_yf)
with codecs.open('00_data_sensors/market_prices_api.py', 'w', encoding='utf-8') as f:
    f.write(text)


# 2. Update Terminal Dashboard
with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text_ui = f.read().replace('\r\n', '\n')

old_ui_def = """@st.cache_data(ttl=86400, show_spinner=False)
def get_company_info_cached(ticker: str) -> dict:
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}"""
        
new_ui_def = """HARDCODED_PROFILES = {
    "MC.PA": {"longName": "LVMH", "sector": "Consommation Discrétionnaire", "industry": "Luxe", "country": "France", "summary": "LVMH Moët Hennessy Louis Vuitton est le leader mondial du luxe, possédant un portefeuille unique de plus de 75 maisons prestigieuses dans les vins et spiritueux, la mode, les parfums et la joaillerie."},
    "OR.PA": {"longName": "L'Oréal", "sector": "Consommation de Base", "industry": "Cosmétiques", "country": "France", "summary": "L'Oréal est le leader mondial de la beauté, proposant une large gamme de produits cosmétiques, de soins de la peau et de parfums à travers de multiples marques internationales."},
    "AI.PA": {"longName": "Air Liquide", "sector": "Matériaux", "industry": "Gaz Industriels", "country": "France", "summary": "Air Liquide est un leader mondial des gaz, technologies et services pour l'industrie et la santé, essentiel à la transition énergétique et à l'innovation industrielle."},
    "TTE.PA": {"longName": "TotalEnergies", "sector": "Énergie", "industry": "Pétrole & Gaz", "country": "France", "summary": "TotalEnergies est une compagnie multi-énergies mondiale de production et de fourniture d'énergies : pétrole et biocarburants, gaz naturel et gaz verts, renouvelables et électricité."},
    "SAN.PA": {"longName": "Sanofi", "sector": "Santé", "industry": "Produits Pharmaceutiques", "country": "France", "summary": "Sanofi est une entreprise mondiale de la santé, innovante et guidée par un objectif : poursuivre les miracles de la science pour améliorer la vie des gens."},
    "ASML.AS": {"longName": "ASML", "sector": "Technologie", "industry": "Équipements Semi-conducteurs", "country": "Pays-Bas", "summary": "ASML est un acteur clé de l'industrie des semi-conducteurs, fournissant aux fabricants de puces le matériel, les logiciels et les services nécessaires à la production en masse de modèles sur silicium."},
    "SAP.DE": {"longName": "SAP", "sector": "Technologie", "industry": "Logiciels d'Entreprise", "country": "Allemagne", "summary": "SAP est l'un des principaux producteurs mondiaux de logiciels pour la gestion des processus métier, développant des solutions qui facilitent le traitement efficace des données et les flux d'informations."},
    "RMS.PA": {"longName": "Hermès", "sector": "Consommation Discrétionnaire", "industry": "Luxe", "country": "France", "summary": "Hermès est une maison de luxe française indépendante, familiale et artisanale, célèbre pour ses produits en cuir, ses accessoires de mode, sa parfumerie et ses montres."},
    "AIR.PA": {"longName": "Airbus", "sector": "Industrie", "industry": "Aérospatial", "country": "France", "summary": "Airbus est un pionnier mondial de l'aéronautique et de l'espace, offrant des solutions innovantes en matière d'avions commerciaux, d'hélicoptères, de défense et d'espace."},
    "BNP.PA": {"longName": "BNP Paribas", "sector": "Finance", "industry": "Banque", "country": "France", "summary": "BNP Paribas est l'une des principales banques européennes avec une présence internationale, offrant des services bancaires de détail, des solutions d'investissement et de financement de marché."},
    "SU.PA": {"longName": "Schneider Electric", "sector": "Industrie", "industry": "Équipements Électriques", "country": "France", "summary": "Schneider Electric est un spécialiste mondial de la gestion de l'énergie et des automatismes, fournissant des solutions numériques pour l'efficacité et la durabilité."},
    "CS.PA": {"longName": "AXA", "sector": "Finance", "industry": "Assurance", "country": "France", "summary": "AXA est un leader mondial de l'assurance et de la gestion d'actifs, accompagnant ses clients dans 51 pays avec des solutions de protection, de santé et d'épargne."},
    "DG.PA": {"longName": "Vinci", "sector": "Industrie", "industry": "Construction & Concessions", "country": "France", "summary": "Vinci est un acteur mondial des métiers des concessions, de l'énergie et de la construction, contribuant à transformer les villes et les territoires."},
    "SAF.PA": {"longName": "Safran", "sector": "Industrie", "industry": "Aérospatial", "country": "France", "summary": "Safran est un groupe international de haute technologie opérant dans les domaines de l'aéronautique (propulsion, équipements et intérieurs), de l'espace et de la défense."}
}

def get_static_profile(ticker: str) -> dict:
    if ticker in HARDCODED_PROFILES:
        return HARDCODED_PROFILES[ticker]
    return {
        "longName": ticker,
        "sector": "Inconnu",
        "industry": "Inconnu",
        "country": "Inconnu",
        "summary": "Données statiques non renseignées pour cette valeur."
    }"""
text_ui = text_ui.replace(old_ui_def, new_ui_def)

old_ui_call = """            try:
                info = get_company_info_cached(selected_ticker)
                name = info.get("longName", selected_ticker)
                sector = info.get("sector", "N/A")
                industry = info.get("industry", "N/A")
                country = info.get("country", "N/A")
                summary = info.get("longBusinessSummary", "")
                
                col_info_left, col_info_right = st.columns([0.4, 0.6])
                with col_info_left:
                    st.markdown(f"### {name}")
                    st.markdown(f"**🌍 Origin:** {country}")
                    st.markdown(f"**🏭 Sector:** {sector}")
                    st.markdown(f"**⚙️ Industry:** {industry}")
                with col_info_right:
                    trunc_summary = summary[:400] + "..." if len(summary) > 400 else summary
                    st.markdown(f"**📖 Business Summary:**<br>_{trunc_summary}_", unsafe_allow_html=True)
                st.markdown("---")
            except Exception as e:
                st.warning("Profile temporarily unavailable.")"""
                
new_ui_call = """            try:
                info = get_static_profile(selected_ticker)
                name = info.get("longName", selected_ticker)
                sector = info.get("sector", "Inconnu")
                industry = info.get("industry", "Inconnu")
                country = info.get("country", "Inconnu")
                summary = info.get("summary", "Données statiques non renseignées pour cette valeur.")
                
                col_info_left, col_info_right = st.columns([0.4, 0.6])
                with col_info_left:
                    st.markdown(f"### {name}")
                    st.markdown(f"**🌍 Origine:** {country}")
                    st.markdown(f"**🏭 Secteur:** {sector}")
                    st.markdown(f"**⚙️ Industrie:** {industry}")
                with col_info_right:
                    trunc_summary = summary[:400] + "..." if len(summary) > 400 else summary
                    st.markdown(f"**📖 Description:**<br>_{trunc_summary}_", unsafe_allow_html=True)
                st.markdown("---")
            except Exception as e:
                st.warning("Profile temporarily unavailable.")"""

text_ui = text_ui.replace(old_ui_call, new_ui_call)

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text_ui)

print("done")
