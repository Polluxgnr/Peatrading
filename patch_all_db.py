import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix 1: Market Pulse News
old_pulse_news = """    try:
        import sqlite3
        conn = sqlite3.connect("data/portfolio.db")
        try:
            news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_master ORDER BY published_at DESC LIMIT 50"
            news_df = pd.read_sql(news_query, conn)
        except sqlite3.OperationalError:
            try:
                news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_history ORDER BY published_at DESC LIMIT 50"
                news_df = pd.read_sql(news_query, conn)
            except sqlite3.OperationalError:
                news_df = pd.DataFrame()
        conn.close()"""
new_pulse_news = """    try:
        import sqlite3
        import pandas as pd
        db = get_portfolio_db()
        with db._connect() as conn:
            try:
                news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_master ORDER BY published_at DESC LIMIT 50"
                news_df = pd.read_sql(news_query, conn)
            except sqlite3.OperationalError:
                try:
                    news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_history ORDER BY published_at DESC LIMIT 50"
                    news_df = pd.read_sql(news_query, conn)
                except sqlite3.OperationalError:
                    news_df = pd.DataFrame()"""

# Fix 2: Deep Dive News
old_dive_news = """                try:
                    import sqlite3
                    import pandas as pd
                    conn = sqlite3.connect("data/portfolio.db")
                    try:
                        n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_master WHERE ticker = ? ORDER BY published_at DESC LIMIT 5"
                        n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                    except sqlite3.OperationalError:
                        try:
                            n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_history WHERE ticker = ? ORDER BY published_at DESC LIMIT 5"
                            n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                        except sqlite3.OperationalError:
                            n_df = pd.DataFrame()
                    conn.close()"""
new_dive_news = """                try:
                    import sqlite3
                    import pandas as pd
                    db = get_portfolio_db()
                    with db._connect() as conn:
                        try:
                            n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_master WHERE ticker = ? ORDER BY published_at DESC LIMIT 5"
                            n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                        except sqlite3.OperationalError:
                            try:
                                n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_history WHERE ticker = ? ORDER BY published_at DESC LIMIT 5"
                                n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                            except sqlite3.OperationalError:
                                n_df = pd.DataFrame()"""

# Fix 3: Ledger
old_ledger = """    try:
        import sqlite3
        conn = sqlite3.connect("data/portfolio.db")
        try:
            df_closed = pd.read_sql("SELECT id, ticker, action, quantity, price, pnl_pct, hold_days, reason, post_mortem, created_at FROM audit_logs WHERE status='CLOSED' ORDER BY created_at DESC", conn)
        except sqlite3.OperationalError:
            df_closed = pd.DataFrame()
        conn.close()"""
new_ledger = """    try:
        import sqlite3
        import pandas as pd
        db = get_portfolio_db()
        with db._connect() as conn:
            try:
                df_closed = pd.read_sql("SELECT id, ticker, action, quantity, price, pnl_pct, hold_days, reason, post_mortem, created_at FROM audit_logs WHERE status='CLOSED' ORDER BY created_at DESC", conn)
            except sqlite3.OperationalError:
                df_closed = pd.DataFrame()"""

text = text.replace(old_pulse_news, new_pulse_news)
text = text.replace(old_dive_news, new_dive_news)
text = text.replace(old_ledger, new_ledger)

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)
