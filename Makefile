.PHONY: deploy test
# Deploy a strategy to QuantConnect: compile + backtest + print stats.
#   make deploy STRATEGY=quantconnect/sid_quantconnect_experiments.py
#   make deploy STRATEGY=path/to/algo.py ARGS="--params side=long start_year=2024"
deploy:
	python3 quantconnect/deploy.py $(STRATEGY) $(ARGS)

test:
	python3 -m pytest tests/ -q
