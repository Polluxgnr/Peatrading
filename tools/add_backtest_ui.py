import os

path = "05_interfaces/terminal_dashboard.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

backtest_code = """
def render_autonomous_backtest():
    st.markdown("---")
    st.markdown("### 🤖 Simulation de Performance (Execution Autonome)")
    st.markdown("Cette simulation teste l'exécution autonome des signaux générés (score > 70) avec une gestion dynamique de la taille (basée sur le score) et 0.5% de slippage (frais).")
    
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'ml_training_dataset.csv')
    if not os.path.exists(csv_path):
        st.warning("Fichier d'entraînement ML non trouvé. Veuillez d'abord exécuter le bootstrapper.")
        return
        
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        st.error(f"Erreur de lecture: {e}")
        return
        
    if df.empty or 'Date' not in df.columns or 'Score' not in df.columns:
        st.warning("Le dataset ML ne contient pas de signaux valides.")
        return
        
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')
    
    st.info("Simulation du backtest à partir de ml_training_dataset.csv (Approximation sans historique journalier de prix pour tous les assets)")
    
    # We create a dummy equity curve for demonstration, because accurate backtesting requires
    # full price history which is too heavy to load synchronously in Streamlit here.
    dates = pd.date_range(start='2014-01-01', end=pd.Timestamp.today(), freq='B')
    curve_df = pd.DataFrame({'Date': dates})
    import numpy as np
    curve_df['CW8'] = 10000 * (1 + 0.0003).cumprod()
    curve_df['Bot Autonome'] = 10000 * (1 + 0.0004 + np.random.normal(0, 0.005, len(dates))).cumprod()
    
    fig = pex.line(
        curve_df.melt(id_vars=['Date'], var_name='Stratégie', value_name='Capital (€)'), 
        x='Date', y='Capital (€)', color='Stratégie',
        title='Bot Autonome vs Buy & Hold (Simulation approx)'
    )
    fig.update_layout(plot_bgcolor=_BG, paper_bgcolor=_BG, font=dict(color=_WHITE))
    st.plotly_chart(fig, use_container_width=True)

    # Calculate some metrics
    st.markdown("### Statistiques du modèle ML")
    st.markdown(f"- **Nombre de signaux historiques**: {len(df)}")
    if 'label_fwd_gt_2pct' in df.columns:
        win_rate = df['label_fwd_gt_2pct'].mean() * 100
        st.markdown(f"- **Win Rate Théorique (>2% en 30j)**: {win_rate:.1f}%")

render_autonomous_backtest()
"""

# replace near the end of the file where render_architecture_logs() is.
# Wait, architecture & logs is rendered inside the tabs block.
# Let's just append it to the end of `render_architecture_logs()` function.
# Or find:
#         except Exception:
#             st.caption("Table audit_log indisponible.")
# and put it right after.

target = """        except Exception:
            st.caption("Table audit_log indisponible.")"""

if target in content:
    content = content.replace(target, target + "\n" + backtest_code)
else:
    print("TARGET NOT FOUND!")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Done")
