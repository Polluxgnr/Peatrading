import os

def build_dump(target_file, is_dashboard_only=False):
    files_to_dump = []
    for root, dirs, files in os.walk('.'):
        if '.git' in root or 'venv' in root or '__pycache__' in root or 'database' in root or '.gemini' in root:
            continue
        for f in files:
            if f.endswith('.py') or f.endswith('.md') or f.endswith('.yml') or f.endswith('.ps1') or f.endswith('.txt'):
                if 'DUMP' not in f and f != 'README.md':
                    # Filter for dashboard only if requested
                    if is_dashboard_only and 'terminal_dashboard.py' not in f and 'components.html' not in f:
                        continue
                        
                    files_to_dump.append(os.path.join(root, f))

    files_to_dump.sort()
    with open(target_file, 'w', encoding='utf-8') as out:
        out.write(f'# PEA Pollux - {"Dashboard " if is_dashboard_only else "Full Project "}Dump\n\n')
        for f in files_to_dump:
            try:
                with open(f, 'r', encoding='utf-8') as infile:
                    content = infile.read()
                ext = f.split('.')[-1]
                lang = 'python' if ext == 'py' else 'markdown' if ext == 'md' else 'yaml' if ext == 'yml' else 'powershell' if ext == 'ps1' else 'text'
                out.write(f'## File: {f}\n\n```{lang}\n{content}\n```\n\n')
            except Exception as e:
                pass

build_dump('PROJECT_FULL_DUMP_FOR_LLM.md', is_dashboard_only=False)
build_dump('DASHBOARD_FULL_DUMP_FOR_LLM.md', is_dashboard_only=True)
print("Dumps updated successfully.")
