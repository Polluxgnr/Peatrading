import pandas as pd
import re

# file 1: technical_scorer.py
path1 = r'02_quant_engine\technical_scorer.py'
with open(path1, 'r', encoding='utf-8') as f:
    code1 = f.read()

# Remove imports
code1 = re.sub(r'(\n\s*import pandas_ta(?!_)[^\n]*\n)', '\n', code1)
code1 = re.sub(r'(\n\s*import pandas_ta_classic[^\n]*\n)', '\n', code1)

helpers = '''
def _calc_sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(window=length, min_periods=1).mean()

def _calc_rsi(s: pd.Series, length: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def _calc_macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, histogram, signal_line

def _calc_bbands(s: pd.Series, length: int = 5, std: float = 2.0):
    sma_line = s.rolling(window=length, min_periods=1).mean()
    std_line = s.rolling(window=length, min_periods=1).std(ddof=0)
    lower = sma_line - (std * std_line)
    upper = sma_line + (std * std_line)
    return lower, sma_line, upper

def _calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()
'''

if '_calc_sma' not in code1:
    code1 = code1.replace('class TechnicalScorer:', helpers + '\nclass TechnicalScorer:')

old_calc = '''        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        out.ta.macd(close=close, append=True)
        out.ta.bbands(close=close, append=True)
        out.ta.atr(high=out["High"], low=out["Low"], close=close, length=14, append=True)'''

new_calc = '''        out["SMA_5"] = _calc_sma(close, 5)
        out["SMA_50"] = _calc_sma(close, 50)
        out["SMA_200"] = _calc_sma(close, 200)
        out["RSI_14"] = _calc_rsi(close, 14)
        
        macd_line, macd_hist, macd_sig = _calc_macd(close)
        out["MACD_12_26_9"] = macd_line
        out["MACDh_12_26_9"] = macd_hist
        out["MACDs_12_26_9"] = macd_sig
        
        bbl, bbm, bbu = _calc_bbands(close)
        out["BBL_5_2.0"] = bbl
        out["BBM_5_2.0"] = bbm
        out["BBU_5_2.0"] = bbu
        
        out["ATRr_14"] = _calc_atr(out["High"], out["Low"], close, 14)'''

code1 = code1.replace(old_calc, new_calc)

with open(path1, 'w', encoding='utf-8') as f:
    f.write(code1)

# file 2: monthly_rebalancer.py
path2 = r'03_risk_portfolio\monthly_rebalancer.py'
with open(path2, 'r', encoding='utf-8') as f:
    code2 = f.read()

# Remove imports
code2 = re.sub(r'(\n\s*import pandas_ta(?!_)[^\n]*\n)', '\n', code2)
code2 = re.sub(r'(\n\s*import pandas_ta_classic[^\n]*\n)', '\n', code2)

old_atr = '''            atr = work.ta.atr(
                high=work["High"],
                low=work["Low"],
                close=work["Close"],
                length=14
            )
            if atr is not None and not atr.empty:'''

new_atr = '''            tr1 = work["High"] - work["Low"]
            tr2 = (work["High"] - work["Close"].shift(1)).abs()
            tr3 = (work["Low"] - work["Close"].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1/14, adjust=False).mean()
            
            if atr is not None and not atr.empty:'''

code2 = code2.replace(old_atr, new_atr)

with open(path2, 'w', encoding='utf-8') as f:
    f.write(code2)

print("Patch completed!")
