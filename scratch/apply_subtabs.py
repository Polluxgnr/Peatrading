import os
import re

path = "05_interfaces/terminal_dashboard.py"

with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

out_lines = []
in_ticker_fiche = False
sub_tab_defined = False

i = 0
while i < len(lines):
    line = lines[i]
    
    if "Qui est {dossier.get('name')}" in line:
        in_ticker_fiche = True
        
    if in_ticker_fiche and not sub_tab_defined and "unsafe_allow_html=True," in line:
        out_lines.append(line)
        i += 1
        out_lines.append(lines[i]) # the closing parenthesis
        i += 1
        
        # Insert sub-tabs
        out_lines.append("    sub_overview, sub_fin, sub_news = st.tabs(['📈 Overview & Charts', '🧠 Financials & AI Scoring', '📰 News & Catalysts'])\n")
        out_lines.append("    with sub_news:\n")
        sub_tab_defined = True
        continue
        
    if sub_tab_defined:
        # Check for section boundaries to switch tabs
        if "### 📖 Catalyseurs & risques" in line:
            # We are already in sub_news
            line = line.replace("### 📖", "#### 📖")
            out_lines.append("    " + line)
            i += 1
            continue
            
        if "Lancer un Red Teaming IA" in line:
            # Still in news/catalysts
            out_lines.append("    " + line)
            i += 1
            continue
            
        if "ind = get_indicators(selected)" in line:
            # Switch to sub_fin
            out_lines.append("    with sub_fin:\n")
            out_lines.append("        " + line.lstrip())
            i += 1
            continue
            
        if "Full-width TradingView chart" in line:
            # Switch to sub_overview
            out_lines.append("    with sub_overview:\n")
            out_lines.append("        " + line.lstrip())
            i += 1
            continue
            
        if "📰 Flux d'actualités croisé" in line:
            # Back to news
            out_lines.append("    with sub_news:\n")
            out_lines.append("        " + line.lstrip())
            i += 1
            continue
            
        if "Tab: Full Universe" in line or "Tab: Architecture & Documentation" in line or "with tab_macro:" in line or "with tab_sys_logs:" in line:
            in_ticker_fiche = False
            sub_tab_defined = False
            out_lines.append(line)
            i += 1
            continue
            
        # If we are in the ticker fiche and a sub-tab is active, we must indent
        if in_ticker_fiche and sub_tab_defined and line.strip() != "":
            # Only add 4 spaces to the existing indentation
            out_lines.append("    " + line)
        else:
            out_lines.append(line)
    else:
        out_lines.append(line)
        
    i += 1

with open(path, "w", encoding="utf-8") as f:
    f.writelines(out_lines)

print("Applied sub-tabs!")
