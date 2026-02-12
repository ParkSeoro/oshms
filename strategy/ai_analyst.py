"""AI 분석가 모듈.

OpenAI GPT를 활용하여 전문가 수준의 주식 분석 코멘터리를 생성한다.
기술적 분석 데이터를 종합하여 자연어로 해석하고,
투자 전략과 리스크를 평가한다.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any

import requests

from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.ai")


@dataclass
class AIAnalysisResult:
    """AI 분석 결과."""
    summary: str = ""           # 종합 요약 (1-2문장)
    detailed_analysis: str = "" # 상세 분석
    risk_assessment: str = ""   # 리스크 평가
    action_plan: str = ""       # 행동 계획
    confidence_comment: str = "" # 신뢰도 코멘트
    market_outlook: str = ""    # 시장 전망
    key_factors: list[str] = field(default_factory=list)  # 핵심 판단 요인
    ai_decision: str = ""       # AI 판정 (STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL)
    ai_score: float = 0.0       # AI 점수 (-1.0 ~ +1.0)
    provider: str = ""          # "openai" or "rule_based"

    def format_report(self) -> str:
        """보기 좋은 형식의 리포트를 생성한다."""
        lines = []
        lines.append("=" * 56)
        lines.append("  AI 전문가 분석 리포트")
        lines.append("=" * 56)

        if self.summary:
            lines.append(f"\n[종합 요약]")
            lines.append(f"  {self.summary}")

        if self.ai_decision:
            lines.append(f"\n[AI 판정] {self.ai_decision} (점수: {self.ai_score:+.2f})")

        if self.detailed_analysis:
            lines.append(f"\n[상세 분석]")
            for line in self.detailed_analysis.split('\n'):
                lines.append(f"  {line}")

        if self.risk_assessment:
            lines.append(f"\n[리스크 평가]")
            for line in self.risk_assessment.split('\n'):
                lines.append(f"  {line}")

        if self.action_plan:
            lines.append(f"\n[행동 계획]")
            for line in self.action_plan.split('\n'):
                lines.append(f"  {line}")

        if self.key_factors:
            lines.append(f"\n[핵심 판단 요인]")
            for f in self.key_factors:
                lines.append(f"  • {f}")

        if self.market_outlook:
            lines.append(f"\n[시장 전망]")
            lines.append(f"  {self.market_outlook}")

        if self.confidence_comment:
            lines.append(f"\n[신뢰도 평가]")
            lines.append(f"  {self.confidence_comment}")

        lines.append(f"\n  분석 엔진: {self.provider}")
        lines.append("=" * 56)
        return "\n".join(lines)


class AIAnalyst:
    """AI 기반 주식 분석가.

    OpenAI GPT를 사용하여 기술적 분석 데이터를
    전문가 수준의 자연어 분석으로 변환한다.
    """

    SYSTEM_PROMPT = """당신은 20년 경력의 한국 주식시장 전문 애널리스트입니다.
기술적 분석 데이터를 받아 전문적인 투자 분석을 제공합니다.

분석 원칙:
1. 데이터 기반의 객관적 분석을 우선합니다
2. 리스크를 항상 명시합니다
3. 구체적인 가격대와 수치를 언급합니다
4. 한국어로 명확하고 간결하게 작성합니다
5. 매수/매도 시점, 목표가, 손절가를 구체적으로 제시합니다

반드시 아래 JSON 형식으로만 응답하세요:
{
    "summary": "1-2문장 종합 요약",
    "decision": "STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL",
    "score": -1.0에서 1.0 사이 숫자,
    "detailed_analysis": "3-5문장 상세 기술적 분석",
    "risk_assessment": "주요 리스크 2-3개",
    "action_plan": "구체적 행동 계획 (진입가, 목표가, 손절가)",
    "key_factors": ["핵심요인1", "핵심요인2", "핵심요인3"],
    "market_outlook": "시장 전망 1-2문장",
    "confidence_comment": "분석 신뢰도에 대한 코멘트"
}"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.session = requests.Session()

    @property
    def is_available(self) -> bool:
        """AI 분석이 사용 가능한지 확인한다."""
        return bool(self.api_key) or bool(self.anthropic_key)

    def analyze(self, analysis_data: dict) -> AIAnalysisResult:
        """AI 분석을 수행한다.

        Args:
            analysis_data: ExpertAnalysis에서 추출한 분석 데이터 딕셔너리

        Returns:
            AIAnalysisResult
        """
        # OpenAI 시도
        if self.api_key:
            try:
                return self._openai_analysis(analysis_data)
            except Exception as e:
                logger.warning("OpenAI 분석 실패: %s", e)

        # Anthropic Claude 시도
        if self.anthropic_key:
            try:
                return self._anthropic_analysis(analysis_data)
            except Exception as e:
                logger.warning("Anthropic 분석 실패: %s", e)

        # 폴백: 규칙 기반 (항상 작동)
        return self._rule_based_analysis(analysis_data)

    def _openai_analysis(self, data: dict) -> AIAnalysisResult:
        """OpenAI API를 사용하여 분석한다."""
        user_prompt = self._build_prompt(data)

        response = self.session.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1000,
            },
            timeout=30,
        )
        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]

        # JSON 파싱
        # content에서 JSON 부분만 추출
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # JSON이 코드블록 안에 있을 수 있음
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                raise ValueError("AI 응답에서 JSON을 파싱할 수 없습니다")

        result = AIAnalysisResult(
            summary=parsed.get("summary", ""),
            detailed_analysis=parsed.get("detailed_analysis", ""),
            risk_assessment=parsed.get("risk_assessment", ""),
            action_plan=parsed.get("action_plan", ""),
            confidence_comment=parsed.get("confidence_comment", ""),
            market_outlook=parsed.get("market_outlook", ""),
            key_factors=parsed.get("key_factors", []),
            ai_decision=parsed.get("decision", "HOLD"),
            ai_score=float(parsed.get("score", 0)),
            provider="openai",
        )

        logger.info("OpenAI 분석 완료: %s (점수=%.2f)", result.ai_decision, result.ai_score)
        return result

    def _anthropic_analysis(self, data: dict) -> AIAnalysisResult:
        """Anthropic Claude API를 사용하여 분석한다."""
        user_prompt = self._build_prompt(data)

        response = self.session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 1000,
                "system": self.SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=30,
        )
        response.raise_for_status()

        content = response.json()["content"][0]["text"]

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                raise ValueError("AI 응답에서 JSON을 파싱할 수 없습니다")

        result = AIAnalysisResult(
            summary=parsed.get("summary", ""),
            detailed_analysis=parsed.get("detailed_analysis", ""),
            risk_assessment=parsed.get("risk_assessment", ""),
            action_plan=parsed.get("action_plan", ""),
            confidence_comment=parsed.get("confidence_comment", ""),
            market_outlook=parsed.get("market_outlook", ""),
            key_factors=parsed.get("key_factors", []),
            ai_decision=parsed.get("decision", "HOLD"),
            ai_score=float(parsed.get("score", 0)),
            provider="anthropic",
        )

        logger.info("Anthropic 분석 완료: %s (점수=%.2f)", result.ai_decision, result.ai_score)
        return result

    def _build_prompt(self, data: dict) -> str:
        """분석 데이터를 프롬프트로 변환한다."""
        lines = [
            f"종목: {data.get('stock_name', '')} ({data.get('stock_code', '')})",
            f"현재가: {data.get('price', 0):,}원",
            f"",
            f"── 기술적 분석 점수 ──",
            f"종합점수: {data.get('total_score', 0):+.3f}",
            f"기술분석: {data.get('technical_score', 0):+.3f}",
            f"패턴분석: {data.get('pattern_score', 0):+.3f}",
            f"뉴스감성: {data.get('sentiment_score', 0):+.3f}",
            f"시장환경: {data.get('market_score', 0):+.3f}",
            f"가격위치: {data.get('price_level_score', 0):+.3f}",
            f"현재판정: {data.get('decision', 'HOLD')}",
            f"신뢰도: {data.get('confidence', 0):.0%}",
        ]

        # 기술적 지표
        tech = data.get("technical", {})
        if tech:
            lines.append(f"")
            lines.append(f"── 기술적 지표 ──")
            lines.append(f"SMA(5/20/60): {tech.get('sma_5', 0):,.0f} / {tech.get('sma_20', 0):,.0f} / {tech.get('sma_60', 0):,.0f}")
            lines.append(f"RSI: {tech.get('rsi', 0):.1f}")
            lines.append(f"MACD: {tech.get('macd_line', 0):,.0f} (신호선: {tech.get('macd_signal', 0):,.0f})")
            lines.append(f"MACD 크로스: {tech.get('macd_cross', 'none')}")
            lines.append(f"스토캐스틱: K={tech.get('stoch_k', 0):.1f} D={tech.get('stoch_d', 0):.1f} ({tech.get('stoch_cross', 'none')})")
            lines.append(f"볼린저 위치: {tech.get('bb_position', 0):.2f}")
            lines.append(f"볼린저: 상단={tech.get('bb_upper', 0):,.0f} 중간={tech.get('bb_middle', 0):,.0f} 하단={tech.get('bb_lower', 0):,.0f}")
            lines.append(f"VWAP: {tech.get('vwap', 0):,.0f}")
            lines.append(f"ATR: {tech.get('atr', 0):,.0f} ({tech.get('atr_pct', 0):.2f}%)")
            lines.append(f"거래량비: x{tech.get('volume_ratio', 0):.1f}")
            lines.append(f"일목균형표: {tech.get('ichimoku_signal', '')}")
            lines.append(f"추세점수: {tech.get('trend_score', 0):+.2f}")
            lines.append(f"모멘텀점수: {tech.get('momentum_score', 0):+.2f}")

            support = tech.get('support_levels', [])
            resistance = tech.get('resistance_levels', [])
            if support:
                lines.append(f"지지선: {', '.join(f'{s:,.0f}' for s in support)}")
            if resistance:
                lines.append(f"저항선: {', '.join(f'{r:,.0f}' for r in resistance)}")

        # 패턴
        patterns = data.get("patterns", [])
        if patterns:
            lines.append(f"")
            lines.append(f"── 캔들스틱 패턴 ──")
            for p in patterns[:5]:
                lines.append(f"  {p.get('name', '')} ({p.get('direction', '')}), 신뢰도={p.get('reliability', 0):.0%}")

        # 뉴스
        news = data.get("news_headlines", [])
        if news:
            lines.append(f"")
            lines.append(f"── 최신 뉴스 ──")
            for h in news[:5]:
                lines.append(f"  {h}")

        # 시장 환경
        market = data.get("market_ctx", {})
        if market:
            lines.append(f"")
            lines.append(f"── 시장 환경 ──")
            lines.append(f"KOSPI 등락: {market.get('kospi_change', 0):+.2f}%")
            lines.append(f"KOSDAQ 등락: {market.get('kosdaq_change', 0):+.2f}%")
            lines.append(f"시장 레짐: {market.get('regime', 'unknown')}")
            lines.append(f"시장 점수: {market.get('market_score', 0):+.2f}")

        lines.append(f"")
        lines.append(f"위 데이터를 종합하여 전문가 분석을 제공해주세요.")

        return "\n".join(lines)

    def _rule_based_analysis(self, data: dict) -> AIAnalysisResult:
        """규칙 기반 분석 (AI API 없을 때 폴백)."""
        result = AIAnalysisResult(provider="rule_based")

        total_score = data.get("total_score", 0)
        confidence = data.get("confidence", 0)
        decision = data.get("decision", "HOLD")
        price = data.get("price", 0)
        stock_name = data.get("stock_name", "")
        tech = data.get("technical", {})

        # AI 판정 (기존 판정 그대로 사용)
        result.ai_decision = decision
        result.ai_score = total_score

        # 종합 요약 생성
        if decision in ("STRONG_BUY", "BUY"):
            result.summary = f"{stock_name} - 기술적 지표가 매수 신호를 보이고 있습니다. 종합점수 {total_score:+.3f}, 신뢰도 {confidence:.0%}."
        elif decision in ("STRONG_SELL", "SELL"):
            result.summary = f"{stock_name} - 매도 신호가 감지되었습니다. 포지션 정리를 검토하세요. 종합점수 {total_score:+.3f}."
        else:
            result.summary = f"{stock_name} - 현재 뚜렷한 방향성이 없습니다. 관망을 추천합니다. 종합점수 {total_score:+.3f}."

        # 상세 분석
        analysis_parts = []
        rsi = tech.get("rsi", 50)
        bb_pos = tech.get("bb_position", 0.5)
        volume_ratio = tech.get("volume_ratio", 1)
        trend = tech.get("trend_score", 0)
        macd_cross = tech.get("macd_cross", "none")

        if rsi < 30:
            analysis_parts.append(f"RSI가 {rsi:.0f}으로 과매도 구간에 진입했습니다. 기술적 반등이 예상됩니다.")
        elif rsi > 70:
            analysis_parts.append(f"RSI가 {rsi:.0f}으로 과매수 구간입니다. 단기 조정 가능성이 있습니다.")
        else:
            analysis_parts.append(f"RSI {rsi:.0f}로 중립적 모멘텀을 보이고 있습니다.")

        if bb_pos < 0.2:
            analysis_parts.append(f"볼린저 밴드 하단(위치 {bb_pos:.2f})에서 지지 가능성이 높습니다.")
        elif bb_pos > 0.8:
            analysis_parts.append(f"볼린저 밴드 상단(위치 {bb_pos:.2f})에서 저항 가능성이 있습니다.")

        if macd_cross == "golden":
            analysis_parts.append("MACD 골든크로스가 발생하여 상승 모멘텀 전환을 시사합니다.")
        elif macd_cross == "dead":
            analysis_parts.append("MACD 데드크로스로 하락 모멘텀 전환이 감지됩니다.")

        if volume_ratio > 2:
            analysis_parts.append(f"거래량이 평균의 {volume_ratio:.1f}배로 급증하여 시장의 관심이 집중되고 있습니다.")

        if trend > 0.3:
            analysis_parts.append("이동평균선 정배열로 상승추세가 견고합니다.")
        elif trend < -0.3:
            analysis_parts.append("이동평균선 역배열로 하락추세가 진행 중입니다.")

        result.detailed_analysis = "\n".join(analysis_parts) if analysis_parts else "분석 데이터가 부족합니다."

        # 리스크 평가
        risks = []
        atr_pct = tech.get("atr_pct", 0)
        if atr_pct > 3:
            risks.append(f"변동성 위험: ATR {atr_pct:.1f}%로 높은 변동성 구간")
        market_ctx = data.get("market_ctx", {})
        if market_ctx.get("regime") == "volatile":
            risks.append("시장 급변: 전체 시장이 불안정한 상태")
        if market_ctx.get("regime") == "trending_down":
            risks.append("하락장 리스크: 시장 전체가 하락추세")
        if confidence < 0.3:
            risks.append(f"낮은 신뢰도: 분석 데이터 부족 ({confidence:.0%})")
        if not risks:
            risks.append("현재 특별한 리스크 요인 없음")
        result.risk_assessment = "\n".join(risks)

        # 행동 계획
        if decision in ("STRONG_BUY", "BUY"):
            support = tech.get("support_levels", [])
            resistance = tech.get("resistance_levels", [])
            entry = price
            target = int(price * 1.03) if not resistance else int(resistance[0])
            stop = int(price * 0.98) if not support else int(support[0] * 0.99)
            result.action_plan = f"진입가: {entry:,}원 부근\n목표가: {target:,}원 (+{(target/price-1)*100:.1f}%)\n손절가: {stop:,}원 ({(stop/price-1)*100:.1f}%)"
        elif decision in ("STRONG_SELL", "SELL"):
            result.action_plan = f"현재가 {price:,}원에서 포지션 축소/정리 권장\n반등 시 추가 매도 고려"
        else:
            result.action_plan = "현재 관망. 명확한 매매 신호 발생 시까지 대기."

        # 핵심 판단 요인
        reasons = data.get("reasons", [])
        result.key_factors = reasons[:5] if reasons else ["분석 데이터 기반 종합 판단"]

        # 시장 전망
        if market_ctx:
            regime = market_ctx.get("regime", "unknown")
            regime_text = {
                "trending_up": "상승추세",
                "trending_down": "하락추세",
                "ranging": "횡보",
                "volatile": "급변",
            }.get(regime, "불확실")
            result.market_outlook = f"시장 레짐: {regime_text}. {'매수 유리한 환경' if regime == 'trending_up' else '보수적 접근 필요' if regime in ('trending_down', 'volatile') else '단기 매매 적합'}"
        else:
            result.market_outlook = "시장 컨텍스트 데이터 없음"

        # 신뢰도 코멘트
        if confidence >= 0.7:
            result.confidence_comment = f"높은 신뢰도 ({confidence:.0%}): 다수의 지표가 일치된 신호를 보내고 있습니다."
        elif confidence >= 0.4:
            result.confidence_comment = f"보통 신뢰도 ({confidence:.0%}): 일부 지표가 상반된 신호를 보이고 있어 주의가 필요합니다."
        else:
            result.confidence_comment = f"낮은 신뢰도 ({confidence:.0%}): 분석 데이터가 부족하거나 상충되고 있습니다. 소액 매매 또는 관망을 권장합니다."

        return result

    @staticmethod
    def extract_analysis_data(analysis) -> dict:
        """ExpertAnalysis 객체에서 AI 분석용 데이터를 추출한다."""
        data = {
            "stock_code": analysis.stock_code,
            "stock_name": analysis.stock_name,
            "price": analysis.price,
            "total_score": analysis.total_score,
            "confidence": analysis.confidence,
            "technical_score": analysis.technical_score,
            "pattern_score": analysis.pattern_score,
            "sentiment_score": analysis.sentiment_score,
            "market_score": analysis.market_score,
            "price_level_score": analysis.price_level_score,
            "decision": analysis.decision,
            "reasons": analysis.reasons,
        }

        # 기술적 지표
        if analysis.technical:
            t = analysis.technical
            data["technical"] = {
                "sma_5": t.sma_5, "sma_20": t.sma_20, "sma_60": t.sma_60,
                "rsi": t.rsi,
                "macd_line": t.macd_line, "macd_signal": t.macd_signal,
                "macd_cross": t.macd_cross,
                "stoch_k": t.stoch_k, "stoch_d": t.stoch_d,
                "stoch_cross": t.stoch_cross,
                "bb_position": t.bb_position,
                "bb_upper": t.bb_upper, "bb_middle": t.bb_middle, "bb_lower": t.bb_lower,
                "vwap": t.vwap,
                "atr": t.atr, "atr_pct": t.atr_pct,
                "volume_ratio": t.volume_ratio,
                "ichimoku_signal": t.ichimoku_signal,
                "trend_score": t.trend_score,
                "momentum_score": t.momentum_score,
                "support_levels": t.support_levels,
                "resistance_levels": t.resistance_levels,
            }

        # 패턴
        if analysis.patterns:
            data["patterns"] = [
                {"name": p.name_kr, "direction": p.direction.value if hasattr(p.direction, 'value') else str(p.direction), "reliability": p.reliability}
                for p in analysis.patterns[:5]
            ]

        # 뉴스
        if analysis.sentiment and analysis.sentiment.key_headlines:
            data["news_headlines"] = analysis.sentiment.key_headlines

        # 시장 환경
        if analysis.market_ctx:
            m = analysis.market_ctx
            data["market_ctx"] = {
                "kospi_change": m.kospi_change,
                "kosdaq_change": m.kosdaq_change,
                "regime": m.regime,
                "market_score": m.market_score,
            }

        return data
