import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

# I will just write a regex to replace HARDCODED_PROFILES block and get_static_profile function
import re

# Find the start of HARDCODED_PROFILES
start_idx = text.find('HARDCODED_PROFILES = {')
end_idx = text.find('with tab_ticker_deep_dive:')

if start_idx != -1 and end_idx != -1:
    new_block = '''HARDCODED_PROFILES = {
    "MC.PA": {"longName": "LVMH", "sector": "Consommation Discrétionnaire", "industry": "Luxe", "country": "France", "longBusinessSummary": "LVMH Moët Hennessy Louis Vuitton est le leader mondial du luxe, possédant un portefeuille unique de plus de 75 maisons prestigieuses dans les vins et spiritueux, la mode, les parfums et la joaillerie."},
    "OR.PA": {"longName": "L'Oréal", "sector": "Consommation de Base", "industry": "Cosmétiques", "country": "France", "longBusinessSummary": "L'Oréal est le leader mondial de la beauté, proposant une large gamme de produits cosmétiques, de soins de la peau et de parfums à travers de multiples marques internationales."},
    "AI.PA": {"longName": "Air Liquide", "sector": "Matériaux", "industry": "Gaz Industriels", "country": "France", "longBusinessSummary": "Air Liquide est un leader mondial des gaz, technologies et services pour l'industrie et la santé, essentiel à la transition énergétique et à l'innovation industrielle."},
    "TTE.PA": {"longName": "TotalEnergies", "sector": "Énergie", "industry": "Pétrole & Gaz", "country": "France", "longBusinessSummary": "TotalEnergies est une compagnie multi-énergies mondiale de production et de fourniture d'énergies : pétrole et biocarburants, gaz naturel et gaz verts, renouvelables et électricité."},
    "SAN.PA": {"longName": "Sanofi", "sector": "Santé", "industry": "Produits Pharmaceutiques", "country": "France", "longBusinessSummary": "Sanofi est une entreprise mondiale de la santé, innovante et guidée par un objectif : poursuivre les miracles de la science pour améliorer la vie des gens."},
    "ASML.AS": {"longName": "ASML", "sector": "Technologie", "industry": "Équipements Semi-conducteurs", "country": "Pays-Bas", "longBusinessSummary": "ASML est un acteur clé de l'industrie des semi-conducteurs, fournissant aux fabricants de puces le matériel, les logiciels et les services nécessaires à la production en masse de modèles sur silicium."},
    "SAP.DE": {"longName": "SAP", "sector": "Technologie", "industry": "Logiciels d'Entreprise", "country": "Allemagne", "longBusinessSummary": "SAP est l'un des principaux producteurs mondiaux de logiciels pour la gestion des processus métier, développant des solutions qui facilitent le traitement efficace des données et les flux d'informations."},
    "RMS.PA": {"longName": "Hermès", "sector": "Consommation Discrétionnaire", "industry": "Luxe", "country": "France", "longBusinessSummary": "Hermès est une maison de luxe française indépendante, familiale et artisanale, célèbre pour ses produits en cuir, ses accessoires de mode, sa parfumerie et ses montres."},
    "AIR.PA": {"longName": "Airbus", "sector": "Industrie", "industry": "Aérospatial", "country": "France", "longBusinessSummary": "Airbus est un pionnier mondial de l'aéronautique et de l'espace, offrant des solutions innovantes en matière d'avions commerciaux, d'hélicoptères, de défense et d'espace."},
    "BNP.PA": {"longName": "BNP Paribas", "sector": "Finance", "industry": "Banque", "country": "France", "longBusinessSummary": "BNP Paribas est l'une des principales banques européennes avec une présence internationale, offrant des services bancaires de détail, des solutions d'investissement et de financement de marché."},
    "SU.PA": {"longName": "Schneider Electric", "sector": "Industrie", "industry": "Équipements Électriques", "country": "France", "longBusinessSummary": "Schneider Electric est un spécialiste mondial de la gestion de l'énergie et des automatismes, fournissant des solutions numériques pour l'efficacité et la durabilité."},
    "CS.PA": {"longName": "AXA", "sector": "Finance", "industry": "Assurance", "country": "France", "longBusinessSummary": "AXA est un leader mondial de l'assurance et de la gestion d'actifs, accompagnant ses clients dans 51 pays avec des solutions de protection, de santé et d'épargne."},
    "DG.PA": {"longName": "Vinci", "sector": "Industrie", "industry": "Construction & Concessions", "country": "France", "longBusinessSummary": "Vinci est un acteur mondial des métiers des concessions, de l'énergie et de la construction, contribuant à transformer les villes et les territoires."},
    "SAF.PA": {"longName": "Safran", "sector": "Industrie", "industry": "Aérospatial", "country": "France", "longBusinessSummary": "Safran est un groupe international de haute technologie opérant dans les domaines de l'aéronautique (propulsion, équipements et intérieurs), de l'espace et de la défense."}
}

def get_company_info(ticker: str) -> dict:
    if ticker in HARDCODED_PROFILES:
        return HARDCODED_PROFILES[ticker]
    return {
        "longName": ticker,
        "country": "Europe",
        "sector": "Inconnu",
        "industry": "Inconnu",
        "longBusinessSummary": "Description statique non renseignée dans le système local."
    }

'''
    
    text = text[:start_idx] + new_block + text[end_idx:]

# Also replace get_static_profile with get_company_info where called
text = text.replace('info = get_static_profile(selected_ticker)', 'info = get_company_info(selected_ticker)')
text = text.replace('summary = info.get("summary", "Données statiques non renseignées pour cette valeur.")', 'summary = info.get("longBusinessSummary", "Description statique non renseignée dans le système local.")')

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)

