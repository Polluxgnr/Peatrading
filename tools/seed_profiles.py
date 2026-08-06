import sys
import os
import sqlite3

# Ensure we can import from the root directory and subdirectories
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, root_dir)
sys.path.insert(0, os.path.join(root_dir, '01_memory_core'))

HARDCODED_PROFILES = {
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

def seed():
    try:
        from sqlite_portfolio import PortfolioDB
        db = PortfolioDB()
        connect_func = db._connect
    except Exception:
        # Fallback if there's a pathing issue
        print("Could not import PortfolioDB. Using direct sqlite3 connection.")
        os.makedirs("database", exist_ok=True)
        import contextlib
        @contextlib.contextmanager
        def fallback_connect():
            conn = sqlite3.connect("database/portfolio.db")
            try:
                yield conn
            finally:
                conn.close()
        connect_func = fallback_connect

    import json
    with connect_func() as conn:
        # Recreate table with correct schema in case the previous script made a flat one
        conn.execute('DROP TABLE IF EXISTS ticker_profiles')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ticker_profiles (
                ticker TEXT PRIMARY KEY,
                profile_json TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        for ticker, data in HARDCODED_PROFILES.items():
            json_string = json.dumps(data, ensure_ascii=False)
            conn.execute('''
                INSERT OR REPLACE INTO ticker_profiles (ticker, profile_json, last_updated)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (ticker, json_string))
            
        conn.commit()
        
    print(f"Successfully seeded {len(HARDCODED_PROFILES)} profiles into ticker_profiles table.")

if __name__ == "__main__":
    seed()
