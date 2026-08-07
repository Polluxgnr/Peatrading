.PHONY: deploy update train test

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

# Forces a live data ingestion and analysis pass
test:
	sudo docker compose exec daemon python main_scheduler.py --now
