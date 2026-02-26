"""OSHMS 웹 서버.

Flask 기반 REST API + 반응형 웹 UI.
PC 브라우저와 모바일(Android) 브라우저 모두 지원.
홈 화면 추가(PWA)로 앱처럼 사용 가능.
"""

import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, render_template, jsonify, request

from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger("oshms.web")

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = os.urandom(24)

# 전역 상태
_state = {
    "trading": False,
    "trader": None,
    "thread": None,
    "logs": [],
    "settings": None,
}


def _get_settings() -> Settings:
    if _state["settings"] is None:
        _state["settings"] = Settings.from_env()
    return _state["settings"]


# ─────────────────── 페이지 ───────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────── REST API ───────────────────

@app.route("/api/status")
def api_status():
    s = _get_settings()
    return jsonify({
        "trading": _state["trading"],
        "mock": s.is_mock,
        "account": f"{s.account_number}-{s.account_suffix}" if s.account_no else "",
        "api_configured": bool(s.app_key and s.app_secret),
    })


@app.route("/api/balance")
def api_balance():
    try:
        from api.kis_api import KISApi
        s = _get_settings()
        api = KISApi(s)
        balance = api.get_balance()
        # 초기 자본금 정보 추가
        balance["initial_capital"] = s.initial_capital
        return jsonify(balance)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json
    code = data.get("code", "")
    name = data.get("name", code)

    try:
        from api.kis_api import KISApi
        from strategy.expert import ExpertStrategy
        from strategy.market_context import MarketContextAnalyzer

        s = _get_settings()
        api = KISApi(s)
        strategy = ExpertStrategy(api=api, settings=s)

        try:
            ctx = MarketContextAnalyzer(api).analyze()
            strategy.set_market_context(ctx)
        except Exception:
            pass

        current_price = api.get_current_price(code)
        if not current_price:
            return jsonify({"error": "시세 조회 실패"}), 400

        current_price["stock_name"] = name

        candles = api.get_minute_chart(code, period="3")
        if len(candles) < 20:
            candles = api.get_daily_chart(code, count=60)
        if not candles:
            return jsonify({"error": "차트 데이터 없음"}), 400

        analysis = strategy.full_analysis(code, name, candles, current_price)

        result = {
            "stock_code": code,
            "stock_name": name,
            "price": analysis.price,
            "decision": analysis.decision,
            "total_score": round(analysis.total_score, 3),
            "confidence": round(analysis.confidence, 2),
            "scores": {
                "technical": round(analysis.technical_score, 3),
                "pattern": round(analysis.pattern_score, 3),
                "sentiment": round(analysis.sentiment_score, 3),
                "market": round(analysis.market_score, 3),
                "price_level": round(analysis.price_level_score, 3),
            },
            "reasons": analysis.reasons,
            "news": analysis.sentiment.key_headlines if analysis.sentiment else [],
            "technical": {
                "rsi": round(analysis.technical.rsi, 1) if analysis.technical else 0,
                "macd": round(analysis.technical.macd_line, 0) if analysis.technical else 0,
                "bb_position": round(analysis.technical.bb_position, 2) if analysis.technical else 0,
                "volume_ratio": round(analysis.technical.volume_ratio, 1) if analysis.technical else 0,
                "ichimoku": analysis.technical.ichimoku_signal if analysis.technical else "",
            },
        }

        # AI 분석 리포트 추가
        try:
            from strategy.ai_analyst import AIAnalyst
            ai = AIAnalyst(api_key=s.openai_api_key)
            ai_data = AIAnalyst.extract_analysis_data(analysis)
            ai_result = ai.analyze(ai_data)
            result["ai_report"] = ai_result.format_report()
            result["ai_decision"] = ai_result.ai_decision
            result["ai_provider"] = ai_result.provider
        except Exception as e:
            logger.warning("AI 분석 실패: %s", e)

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trade/start", methods=["POST"])
def api_trade_start():
    if _state["trading"]:
        return jsonify({"error": "이미 실행 중"}), 400

    data = request.json or {}
    stocks = data.get("stocks", "")
    strategy_name = data.get("strategy", "expert")
    interval = int(data.get("interval", 10))

    def _run():
        import logging

        class WebLogHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                _state["logs"].append(msg)
                if len(_state["logs"]) > 500:
                    _state["logs"] = _state["logs"][-300:]

        handler = WebLogHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger("oshms").addHandler(handler)

        try:
            from api.kis_api import KISApi
            from strategy import ExpertStrategy, ScalpingStrategy, MomentumStrategy, CombinedStrategy
            from trading.trader import AutoTrader

            s = Settings.from_env()
            api = KISApi(s)

            strategy_map = {
                "expert": lambda: ExpertStrategy(api=api, settings=s),
                "scalping": ScalpingStrategy,
                "momentum": MomentumStrategy,
                "combined": CombinedStrategy,
            }

            factory = strategy_map.get(strategy_name, strategy_map["expert"])
            strat = factory()

            trader = AutoTrader(api, s, strat)
            _state["trader"] = trader
            _state["trading"] = True

            target = [t.strip() for t in stocks.split(",") if t.strip()] or None
            trader.start(target_stocks=target, interval=interval)
        except Exception as e:
            _state["logs"].append(f"오류: {e}")
        finally:
            _state["trading"] = False
            _state["trader"] = None
            logging.getLogger("oshms").removeHandler(handler)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    _state["thread"] = thread

    return jsonify({"message": "자동매매 시작"})


@app.route("/api/trade/stop", methods=["POST"])
def api_trade_stop():
    if _state["trader"]:
        _state["trader"].stop()
    _state["trading"] = False
    return jsonify({"message": "중지됨"})


@app.route("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0))
    logs = _state["logs"][since:]
    return jsonify({"logs": logs, "total": len(_state["logs"])})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = _get_settings()
    return jsonify({
        "app_key": "***" if s.app_key else "",
        "account_no": s.account_no,
        "is_mock": s.is_mock,
        "initial_capital": s.initial_capital,
        "max_buy_amount": s.max_buy_amount,
        "max_hold_count": s.max_hold_count,
        "stop_loss_pct": s.stop_loss_pct,
        "take_profit_pct": s.take_profit_pct,
        "trading_start_time": s.trading_start_time,
        "trading_end_time": s.trading_end_time,
        "openai_api_key": "***" if s.openai_api_key else "",
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json
    env_lines = []
    field_map = {
        "app_key": "KIS_APP_KEY",
        "app_secret": "KIS_APP_SECRET",
        "account_no": "KIS_ACCOUNT_NO",
        "is_mock": "KIS_MOCK",
        "initial_capital": "INITIAL_CAPITAL",
        "max_buy_amount": "MAX_BUY_AMOUNT",
        "max_hold_count": "MAX_HOLD_COUNT",
        "stop_loss_pct": "STOP_LOSS_PCT",
        "take_profit_pct": "TAKE_PROFIT_PCT",
        "trading_start_time": "TRADING_START_TIME",
        "trading_end_time": "TRADING_END_TIME",
    }
    for key, env_key in field_map.items():
        val = data.get(key, "")
        if key == "is_mock":
            val = "true" if val else "false"
        env_lines.append(f"{env_key}={val}")
    # AI API 키
    openai_key = data.get("openai_api_key", "")
    if openai_key:
        env_lines.append(f"OPENAI_API_KEY={openai_key}")
    env_lines.append("LOG_LEVEL=INFO")

    Path(".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    _state["settings"] = None
    return jsonify({"message": "저장 완료"})


@app.route("/api/report")
def api_report():
    from analysis.analyzer import ProfitAnalyzer
    analyzer = ProfitAnalyzer()
    return jsonify({
        "summary": analyzer.get_summary(),
        "daily": analyzer.get_daily_summary(),
        "by_stock": analyzer.get_stock_summary(),
    })


def run_server(host="0.0.0.0", port=5000, debug=False):
    """웹 서버를 실행한다."""
    logger.info("OSHMS 웹 서버 시작: http://%s:%d", host, port)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server(debug=True)
