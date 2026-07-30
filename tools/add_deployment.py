import os

path = "main_scheduler.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

deployment_code = """    # --- Phase 49: Intelligent Capital Deployment (80% Rule) ---
    from pea_position_sizer import PeaSizer
    inv_rate = PeaSizer.investment_rate(portfolio)
    if inv_rate < 0.80:
        market_reg = getattr(macro_alpha, "_last_regime_result", None)
        is_bad_regime = False
        if market_reg:
            rm = market_reg.get("regime", "").upper()
            if rm in ("BEAR", "VOLATILE"):
                is_bad_regime = True
        
        if not is_bad_regime:
            logger.info("Invested capital (%.1f%%) < 80%%. Activating strategic deployment.", inv_rate * 100)
            # Find signals that were rejected ONLY because of score threshold
            rejected_for_score = [s for s in processed if s.status == SignalStatus.REJECTED and ("Score" in s.reason or "< 65" in s.reason)]
            rejected_for_score.sort(key=lambda x: x.score, reverse=True)
            
            deployed = 0
            for sig in rejected_for_score:
                if deployed >= 3:
                    break
                price = current_prices.get(sig.ticker, 0.0)
                if price > 0:
                    target_qty, sizing = orchestrator.sizer.size_with_explanation(sig, portfolio, price)
                    if target_qty > 0:
                        sig.target_qty = target_qty
                        sig.status = SignalStatus.APPROVED
                        sig.reason = f"DÉPLOIEMENT STRATÉGIQUE (Cash: {100 - inv_rate*100:.1f}%) | {target_qty} actions @ {price:.2f} EUR (Score: {sig.score:.1f})"
                        logger.info("Strategic deployment APPROVED %s (score=%.1f)", sig.ticker, sig.score)
                        deployed += 1
"""

target = """    approved = [s for s in processed if s.status == SignalStatus.APPROVED]
    logger.info(
        "Orchestrator finalized %d signal(s): %d APPROVED (VIX=%.1f).",
        len(processed),
        len(approved),
        vix_level,
    )"""

if target in content:
    content = content.replace(target, target + "\n" + deployment_code)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Deployment logic inserted successfully.")
else:
    print("Target block not found.")
