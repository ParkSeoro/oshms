"""한국투자증권 Open API 클라이언트.

REST API를 통해 인증, 시세 조회, 주문 실행을 처리한다.
API 문서: https://apiportal.koreainvestment.com
"""

import time
from datetime import datetime, timedelta
from typing import Any

import requests

from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger("oshms.api")


class KISApi:
    """한국투자증권 Open API 래퍼."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.base_url
        self._access_token: str = ""
        self._token_expires_at: datetime = datetime.min
        self.session = requests.Session()

    # ──────────────────────────────────────────────
    # 인증
    # ──────────────────────────────────────────────

    def _ensure_token(self) -> None:
        """토큰이 만료되었으면 재발급한다."""
        if self._access_token and datetime.now() < self._token_expires_at:
            return
        self._issue_token()

    def _issue_token(self) -> None:
        """OAuth 액세스 토큰을 발급받는다."""
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        resp = self.session.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in - 60)
        logger.info("액세스 토큰 발급 완료 (만료: %s)", self._token_expires_at.strftime("%H:%M:%S"))

    def _get_hashkey(self, body: dict) -> str:
        """주문용 해시키를 생성한다."""
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "Content-Type": "application/json",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        resp = self.session.post(url, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()["HASH"]

    def _headers(self, tr_id: str, *, hashkey: str = "") -> dict[str, str]:
        """공통 요청 헤더를 생성한다."""
        self._ensure_token()
        h = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
            "tr_id": tr_id,
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    # ──────────────────────────────────────────────
    # 시세 조회
    # ──────────────────────────────────────────────

    def get_current_price(self, stock_code: str) -> dict[str, Any]:
        """종목의 현재가 정보를 조회한다.

        Returns:
            dict with keys: price, open, high, low, volume, change_rate, etc.
        """
        tr_id = "FHKST01010100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("현재가 조회 실패 [%s]: %s", stock_code, data.get("msg1", ""))
            return {}

        output = data.get("output", {})
        return {
            "stock_code": stock_code,
            "price": int(output.get("stck_prpr", 0)),
            "open": int(output.get("stck_oprc", 0)),
            "high": int(output.get("stck_hgpr", 0)),
            "low": int(output.get("stck_lwpr", 0)),
            "volume": int(output.get("acml_vol", 0)),
            "change_rate": float(output.get("prdy_ctrt", 0)),
            "trade_amount": int(output.get("acml_tr_pbmn", 0)),
            "per": float(output.get("per", 0)),
            "pbr": float(output.get("pbr", 0)),
        }

    def get_minute_chart(self, stock_code: str, period: str = "1") -> list[dict]:
        """분봉 데이터를 조회한다.

        Args:
            stock_code: 종목 코드
            period: 분봉 주기 ("1", "3", "5", "10", "15", "30", "60")

        Returns:
            list of candle dicts (newest first)
        """
        tr_id = "FHKST03010200"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        now = datetime.now().strftime("%H%M%S")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_HOUR_1": now,
            "FID_PW_DATA_INCU_YN": "N",
            "FID_ETC_CLS_CODE": period,
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("분봉 조회 실패 [%s]: %s", stock_code, data.get("msg1", ""))
            return []

        candles = []
        for item in data.get("output2", []):
            candles.append({
                "time": item.get("stck_cntg_hour", ""),
                "open": int(item.get("stck_oprc", 0)),
                "high": int(item.get("stck_hgpr", 0)),
                "low": int(item.get("stck_lwpr", 0)),
                "close": int(item.get("stck_prpr", 0)),
                "volume": int(item.get("cntg_vol", 0)),
            })
        return candles

    def get_daily_chart(self, stock_code: str, period: str = "D", count: int = 60) -> list[dict]:
        """일봉/주봉/월봉 데이터를 조회한다.

        Args:
            stock_code: 종목 코드
            period: "D"(일), "W"(주), "M"(월)
            count: 조회 개수
        """
        tr_id = "FHKST03010100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            return []

        candles = []
        for item in data.get("output2", [])[:count]:
            candles.append({
                "date": item.get("stck_bsop_date", ""),
                "open": int(item.get("stck_oprc", 0)),
                "high": int(item.get("stck_hgpr", 0)),
                "low": int(item.get("stck_lwpr", 0)),
                "close": int(item.get("stck_clpr", 0)),
                "volume": int(item.get("acml_vol", 0)),
            })
        return candles

    def get_volume_rank(self, count: int = 20) -> list[dict]:
        """거래량 상위 종목을 조회한다."""
        tr_id = "FHPST01710000"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "0",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": "",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            return []

        results = []
        for item in data.get("output", [])[:count]:
            results.append({
                "stock_code": item.get("mksc_shrn_iscd", ""),
                "stock_name": item.get("hts_kor_isnm", ""),
                "price": int(item.get("stck_prpr", 0)),
                "change_rate": float(item.get("prdy_ctrt", 0)),
                "volume": int(item.get("acml_vol", 0)),
                "trade_amount": int(item.get("acml_tr_pbmn", 0)),
            })
        return results

    # ──────────────────────────────────────────────
    # 주문
    # ──────────────────────────────────────────────

    def buy_market_order(self, stock_code: str, quantity: int) -> dict[str, Any]:
        """시장가 매수 주문을 실행한다."""
        return self._place_order(stock_code, quantity, order_type="buy", price=0)

    def sell_market_order(self, stock_code: str, quantity: int) -> dict[str, Any]:
        """시장가 매도 주문을 실행한다."""
        return self._place_order(stock_code, quantity, order_type="sell", price=0)

    def buy_limit_order(self, stock_code: str, quantity: int, price: int) -> dict[str, Any]:
        """지정가 매수 주문을 실행한다."""
        return self._place_order(stock_code, quantity, order_type="buy", price=price)

    def sell_limit_order(self, stock_code: str, quantity: int, price: int) -> dict[str, Any]:
        """지정가 매도 주문을 실행한다."""
        return self._place_order(stock_code, quantity, order_type="sell", price=price)

    def _place_order(
        self, stock_code: str, quantity: int, order_type: str, price: int
    ) -> dict[str, Any]:
        """주문을 실행한다. 서버 오류 시 최대 3회 재시도."""
        is_buy = order_type == "buy"

        if self.settings.is_mock:
            tr_id = "VTTC0802U" if is_buy else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if is_buy else "TTTC0311U"

        # 시장가(01) vs 지정가(00)
        ord_dvsn = "01" if price == 0 else "00"

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.settings.account_number,
            "ACNT_PRDT_CD": self.settings.account_suffix,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

        side = "매수" if is_buy else "매도"

        for attempt in range(3):
            try:
                hashkey = self._get_hashkey(body)
                headers = self._headers(tr_id, hashkey=hashkey)

                resp = self.session.post(url, json=body, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                if data.get("rt_cd") == "0":
                    order_no = data.get("output", {}).get("ODNO", "N/A")
                    logger.info(
                        "%s 주문 성공: %s %d주 (주문번호: %s)", side, stock_code, quantity, order_no
                    )
                    return {"success": True, "order_no": order_no, "data": data.get("output", {})}
                else:
                    msg = data.get("msg1", "알 수 없는 오류")
                    logger.error("%s 주문 실패: %s - %s", side, stock_code, msg)
                    return {"success": False, "message": msg}
            except Exception as e:
                logger.warning("%s 주문 오류 (시도 %d/3): %s - %s", side, attempt + 1, stock_code, e)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        logger.error("%s 주문 최종 실패: %s (3회 재시도 초과)", side, stock_code)
        return {"success": False, "message": "서버 오류 (재시도 초과)"}

    # ──────────────────────────────────────────────
    # 잔고 조회
    # ──────────────────────────────────────────────

    def get_balance(self) -> dict[str, Any]:
        """주식 잔고를 조회한다.

        Returns:
            dict with 'holdings' (list) and 'summary' (dict)
        """
        tr_id = "VTTC8434R" if self.settings.is_mock else "TTTC8434R"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.account_number,
            "ACNT_PRDT_CD": self.settings.account_suffix,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("잔고 조회 실패: %s", data.get("msg1", ""))
            return {"holdings": [], "summary": {}}

        holdings = []
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            holdings.append({
                "stock_code": item.get("pdno", ""),
                "stock_name": item.get("prdt_name", ""),
                "quantity": qty,
                "avg_price": int(float(item.get("pchs_avg_pric", 0))),
                "current_price": int(item.get("prpr", 0)),
                "profit_loss": int(item.get("evlu_pfls_amt", 0)),
                "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                "buy_amount": int(item.get("pchs_amt", 0)),
                "eval_amount": int(item.get("evlu_amt", 0)),
            })

        summary_data = data.get("output2", [{}])
        summary_item = summary_data[0] if summary_data else {}
        summary = {
            "total_buy_amount": int(summary_item.get("pchs_amt_smtl_amt", 0)),
            "total_eval_amount": int(summary_item.get("evlu_amt_smtl_amt", 0)),
            "total_profit_loss": int(summary_item.get("evlu_pfls_smtl_amt", 0)),
            "total_profit_rate": float(summary_item.get("tot_evlu_pfls_rt", 0)) if summary_item.get("tot_evlu_pfls_rt") else 0.0,
            "available_cash": int(summary_item.get("dnca_tot_amt", 0)),
        }

        return {"holdings": holdings, "summary": summary}

    def get_order_history(self) -> list[dict]:
        """당일 주문 내역을 조회한다."""
        tr_id = "VTTC8001R" if self.settings.is_mock else "TTTC8001R"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.settings.account_number,
            "ACNT_PRDT_CD": self.settings.account_suffix,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "01",
            "PDNO": "",
            "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "01",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            return []

        orders = []
        for item in data.get("output1", []):
            orders.append({
                "order_no": item.get("odno", ""),
                "stock_code": item.get("pdno", ""),
                "stock_name": item.get("prdt_name", ""),
                "side": "매수" if item.get("sll_buy_dvsn_cd") == "02" else "매도",
                "order_qty": int(item.get("ord_qty", 0)),
                "exec_qty": int(item.get("tot_ccld_qty", 0)),
                "order_price": int(item.get("ord_unpr", 0)),
                "exec_price": int(float(item.get("avg_prvs", 0))),
                "order_time": item.get("ord_tmd", ""),
            })
        return orders

    # ──────────────────────────────────────────────
    # 해외 주식 시세 조회
    # ──────────────────────────────────────────────

    def get_overseas_price(self, market: str, stock_code: str) -> dict[str, Any]:
        """해외 주식의 현재가 정보를 조회한다.

        Args:
            market: 거래소 코드 ("NASD", "NYSE", "AMEX", "SEHK", "TKSE")
            stock_code: 종목 코드 (예: "AAPL", "TSLA")

        Returns:
            dict with keys: stock_code, price, open, high, low, volume, change_rate
        """
        tr_id = "HHDFS76200200"
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        params = {
            "AUTH": "",
            "EXCD": market,
            "SYMB": stock_code,
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("해외 현재가 조회 실패 [%s/%s]: %s", market, stock_code, data.get("msg1", ""))
            return {}

        output = data.get("output", {})
        return {
            "stock_code": stock_code,
            "price": float(output.get("last", 0)),
            "open": float(output.get("open", 0)),
            "high": float(output.get("high", 0)),
            "low": float(output.get("low", 0)),
            "volume": int(output.get("tvol", 0)),
            "change_rate": float(output.get("rate", 0)),
        }

    def get_overseas_daily_chart(
        self, market: str, stock_code: str, period: str = "D", count: int = 60
    ) -> list[dict]:
        """해외 주식 일봉 데이터를 조회한다.

        Args:
            market: 거래소 코드 ("NASD", "NYSE", "AMEX", "SEHK", "TKSE")
            stock_code: 종목 코드
            period: "D"(일), "W"(주), "M"(월)
            count: 조회 개수

        Returns:
            list of candle dicts (newest first)
        """
        tr_id = "FHKST03030100"
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        params = {
            "AUTH": "",
            "EXCD": market,
            "SYMB": stock_code,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "0",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("해외 일봉 조회 실패 [%s/%s]: %s", market, stock_code, data.get("msg1", ""))
            return []

        candles = []
        for item in data.get("output2", [])[:count]:
            candles.append({
                "date": item.get("xymd", ""),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("clos", 0)),
                "volume": int(item.get("tvol", 0)),
            })
        return candles

    # ──────────────────────────────────────────────
    # 해외 주식 주문
    # ──────────────────────────────────────────────

    def buy_overseas_market_order(
        self, market: str, stock_code: str, quantity: int, price: float
    ) -> dict[str, Any]:
        """해외 주식 매수 주문을 실행한다.

        Args:
            market: 거래소 코드 ("NASD", "NYSE", "AMEX", "SEHK", "TKSE")
            stock_code: 종목 코드
            quantity: 주문 수량
            price: 주문 가격
        """
        return self._place_overseas_order(market, stock_code, quantity, order_type="buy", price=price)

    def sell_overseas_market_order(
        self, market: str, stock_code: str, quantity: int, price: float
    ) -> dict[str, Any]:
        """해외 주식 매도 주문을 실행한다.

        Args:
            market: 거래소 코드 ("NASD", "NYSE", "AMEX", "SEHK", "TKSE")
            stock_code: 종목 코드
            quantity: 주문 수량
            price: 주문 가격
        """
        return self._place_overseas_order(market, stock_code, quantity, order_type="sell", price=price)

    def _place_overseas_order(
        self, market: str, stock_code: str, quantity: int, order_type: str, price: float
    ) -> dict[str, Any]:
        """해외 주식 주문을 실행한다. 서버 오류 시 최대 3회 재시도."""
        is_buy = order_type == "buy"

        if self.settings.is_mock:
            tr_id = "VTTT1002U" if is_buy else "VTTT1006U"
        else:
            tr_id = "JTTT1002U" if is_buy else "JTTT1006U"

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        body = {
            "CANO": self.settings.account_number,
            "ACNT_PRDT_CD": self.settings.account_suffix,
            "OVRS_EXCG_CD": market,
            "PDNO": stock_code,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
        }

        side = "해외매수" if is_buy else "해외매도"

        for attempt in range(3):
            try:
                hashkey = self._get_hashkey(body)
                headers = self._headers(tr_id, hashkey=hashkey)

                resp = self.session.post(url, json=body, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                if data.get("rt_cd") == "0":
                    order_no = data.get("output", {}).get("ODNO", "N/A")
                    logger.info(
                        "%s 주문 성공: %s/%s %d주 @%.2f (주문번호: %s)",
                        side, market, stock_code, quantity, price, order_no,
                    )
                    return {"success": True, "order_no": order_no, "data": data.get("output", {})}
                else:
                    msg = data.get("msg1", "알 수 없는 오류")
                    logger.error("%s 주문 실패: %s/%s - %s", side, market, stock_code, msg)
                    return {"success": False, "message": msg}
            except Exception as e:
                logger.warning("%s 주문 오류 (시도 %d/3): %s/%s - %s", side, attempt + 1, market, stock_code, e)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        logger.error("%s 주문 최종 실패: %s/%s (3회 재시도 초과)", side, market, stock_code)
        return {"success": False, "message": "서버 오류 (재시도 초과)"}

    # ──────────────────────────────────────────────
    # 해외 주식 잔고 조회
    # ──────────────────────────────────────────────

    def get_overseas_balance(self, market: str = "") -> dict[str, Any]:
        """해외 주식 잔고를 조회한다.

        Args:
            market: 거래소 코드 (빈 문자열이면 전체 조회)

        Returns:
            dict with 'holdings' (list) and 'summary' (dict)
        """
        tr_id = "VTTS3012R" if self.settings.is_mock else "JTTT3012R"
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.account_number,
            "ACNT_PRDT_CD": self.settings.account_suffix,
            "OVRS_EXCG_CD": market,
            "TR_CRCY_CD": "",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.error("해외 잔고 조회 실패: %s", data.get("msg1", ""))
            return {"holdings": [], "summary": {}}

        holdings = []
        for item in data.get("output1", []):
            qty = int(item.get("ovrs_cblc_qty", 0))
            if qty <= 0:
                continue
            holdings.append({
                "stock_code": item.get("ovrs_pdno", ""),
                "stock_name": item.get("ovrs_item_name", ""),
                "quantity": qty,
                "avg_price": float(item.get("pchs_avg_pric", 0)),
                "current_price": float(item.get("now_pric2", 0)),
                "profit_loss": float(item.get("frcr_evlu_pfls_amt", 0)),
                "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                "buy_amount": float(item.get("frcr_pchs_amt1", 0)),
                "eval_amount": float(item.get("ovrs_stck_evlu_amt", 0)),
                "market": item.get("ovrs_excg_cd", ""),
                "currency": item.get("tr_crcy_cd", ""),
            })

        summary_data = data.get("output2", [{}])
        summary_item = summary_data[0] if summary_data else {}
        summary = {
            "total_buy_amount": float(summary_item.get("frcr_pchs_amt1", 0)),
            "total_eval_amount": float(summary_item.get("ovrs_tot_pfls", 0)),
            "total_profit_loss": float(summary_item.get("ovrs_rlzt_pfls_amt", 0)),
            "total_profit_rate": float(summary_item.get("tot_evlu_pfls_rt", 0)) if summary_item.get("tot_evlu_pfls_rt") else 0.0,
            "available_cash": float(summary_item.get("frcr_dncl_amt_2", 0)),
        }

        return {"holdings": holdings, "summary": summary}
