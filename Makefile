.PHONY: deploy update train test api mcp dashboard scheduler dump

# Regenerates full monolithic and domain-specific LLM context dumps
dump:
	python tools/build_llm_dump.py

# Runs the Internal Recommendation API (FastAPI)
api:
	uvicorn 06_api.internal_api:app --host 0.0.0.0 --port 8000 --reload

# Runs the Model Context Protocol (MCP) Server for Claude Desktop
mcp:
	python 07_mcp/pollux_mcp.py

# Runs the Streamlit Bloomberg HUD Terminal
dashboard:
	streamlit run 05_interfaces/terminal_dashboard.py

# Runs the continuous Paris market scheduler daemon
scheduler:
	python main_scheduler.py

# Runs the full institutional test suite
test:
	python -m unittest discover tests

# Fetches latest code from GitHub and restarts the Docker containers
deploy:
	git fetch origin
	git reset --hard origin/master
	sudo docker compose down
	sudo docker compose up -d --build

# Light update: pulls code and restarts without rebuilding the images
update:
	git fetch origin
	git reset --hard origin/master
	sudo docker compose restart daemon
	sudo docker compose restart dashboard

# Forces an ML training pass
train:
	sudo docker compose exec daemon python 02_quant_engine/ml_trainer.py
