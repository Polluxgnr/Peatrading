import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_until = -1
for i, line in enumerate(lines):
    if i < skip_until:
        continue
        
    if "conn = sqlite3.connect(\"data/portfolio.db\")" in line:
        indent = line[:line.find("conn =")]
        new_lines.append(indent + "import pandas as pd\n")
        new_lines.append(indent + "db = get_portfolio_db()\n")
        new_lines.append(indent + "with db._connect() as conn:\n")
        
        # Now we need to indent everything until conn.close()
        # So we look ahead
        for j in range(i+1, len(lines)):
            if lines[j].strip() == "conn.close()":
                # We indent all lines between i+1 and j
                for k in range(i+1, j):
                    new_lines.append("    " + lines[k])
                skip_until = j + 1
                break
    elif 'st.markdown("### ?? Top Opportunities & Momentum Leaders")' in line:
        new_lines.append(line.replace('??', '🚀'))
    elif 'st.dataframe(pd.DataFrame(opps)' in line:
        indent = line[:line.find("st.dataframe")]
        new_lines.append(indent + "df_opps = pd.DataFrame(opps).head(5)\n")
        new_lines.append(indent + "st.dataframe(df_opps, use_container_width=True, hide_index=True)\n")
    elif 'st.caption("Module unavailable or no data.")' in line or 'st.caption(f"Module unavailable or no data. ({e})")' in line:
        indent = line[:line.find("st.caption")]
        new_lines.append(indent + 'st.info("Data unavailable")\n')
    elif 'moms = get_momentum_pepites()' in line:
        new_lines.append(line.replace("()", "(limit=5)"))
    elif 'st.dataframe(pd.DataFrame(moms)' in line:
        indent = line[:line.find("st.dataframe")]
        new_lines.append(indent + "df_moms = pd.DataFrame(moms)\n")
        new_lines.append(indent + "st.dataframe(df_moms, use_container_width=True, hide_index=True)\n")
    else:
        new_lines.append(line)

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
