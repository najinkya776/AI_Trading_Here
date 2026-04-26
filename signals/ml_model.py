"""
XGBoost ML Signal Classifier.

Trains on historical OHLCV + indicators to predict:
  1 = price will go UP in next N bars (BUY signal)
 -1 = price will go DOWN in next N bars (SELL signal)
  0 = NEUTRAL (skip)

Usage:
    # Train:  python -m signals.ml_model --mode train --symbol NIFTY
    # Predict (in code):
    from signals.ml_model import MLModel
    model = MLModel("NIFTY")
    confidence, direction = model.predict(latest_row)
"""
import os
import argparse
import logging
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report
from xgboost import XGBClassifier

from data.historical import fetch_ohlcv
from signals.technical import add_indicators

log = logging.getLogger(__name__)

MODEL_DIR = "models"
LOOKAHEAD_BARS = 6      # predict 6 bars (30 min on 5m chart) ahead
MIN_MOVE_PCT   = 0.003  # 0.3% move minimum to count as signal

FEATURE_COLS = [
    "rsi", "macd", "macd_signal", "macd_hist",
    "supertrend_dir", "adx", "dmp", "dmn",
    "bb_width", "stoch_k", "stoch_d",
    "vol_ratio", "body_size", "upper_wick", "lower_wick",
    "pct_change",
    # Derived price position features
    "close_vs_ema9", "close_vs_ema21", "close_vs_vwap",
    "ema9_vs_ema21", "close_vs_bb_mid",
]


class MLModel:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.model = None
        self._load()

    def predict(self, row: pd.Series) -> tuple[float, int]:
        """
        Returns (confidence: 0.0–1.0, direction: 1 | -1 | 0).
        Use confidence > 0.6 as threshold for taking trades.
        """
        if self.model is None:
            log.warning("ML model not trained yet — returning NEUTRAL")
            return 0.0, 0

        features = self._extract_features(row)
        if features is None:
            return 0.0, 0

        proba = self.model.predict_proba([features])[0]
        classes = self.model.classes_
        pred_class = classes[np.argmax(proba)]
        confidence = float(np.max(proba))
        return confidence, int(pred_class)

    def train(self, days: int = 365):
        print(f"Training ML model for {self.symbol} on {days} days of data...")
        df = fetch_ohlcv(self.symbol, interval="5m", days=days)
        df = add_indicators(df)
        df = self._add_derived_features(df)
        df = self._add_labels(df)
        df = df.dropna()

        X = df[FEATURE_COLS].values
        y = df["label"].values

        print(f"Dataset: {len(df)} rows | Labels: {pd.Series(y).value_counts().to_dict()}")

        # TimeSeriesSplit — never use future data for training
        tscv = TimeSeriesSplit(n_splits=5)
        scores = []

        model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            model.fit(X[train_idx], y[train_idx], verbose=False)
            acc = model.score(X[val_idx], y[val_idx])
            scores.append(acc)
            print(f"  Fold {fold+1}: accuracy = {acc:.3f}")

        print(f"\nMean CV accuracy: {np.mean(scores):.3f} (+/- {np.std(scores):.3f})")

        # Final fit on all data
        model.fit(X, y, verbose=False)
        print("\nClassification report (in-sample):")
        print(classification_report(y, model.predict(X), target_names=["SELL(-1)", "NEUTRAL(0)", "BUY(1)"]))

        self.model = model
        self._save()
        print(f"Model saved to {self._model_path()}")

    def _add_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """Label = direction of price move LOOKAHEAD_BARS bars ahead."""
        future_close = df["close"].shift(-LOOKAHEAD_BARS)
        pct_move = (future_close - df["close"]) / df["close"]

        conditions = [
            pct_move > MIN_MOVE_PCT,
            pct_move < -MIN_MOVE_PCT,
        ]
        df["label"] = np.select(conditions, [1, -1], default=0)
        return df

    @staticmethod
    def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["close_vs_ema9"]  = (df["close"] - df["ema9"])   / df["atr"]
        df["close_vs_ema21"] = (df["close"] - df["ema21"])  / df["atr"]
        df["close_vs_vwap"]  = (df["close"] - df["vwap"])   / df["atr"]
        df["ema9_vs_ema21"]  = (df["ema9"]  - df["ema21"])  / df["atr"]
        df["close_vs_bb_mid"]= (df["close"] - df["bb_mid"]) / df["atr"]
        return df

    @staticmethod
    def _extract_features(row: pd.Series) -> list | None:
        try:
            return [row[col] for col in FEATURE_COLS]
        except KeyError as e:
            log.warning(f"Missing feature: {e}")
            return None

    def _model_path(self) -> str:
        return os.path.join(MODEL_DIR, f"xgb_{self.symbol.lower()}.pkl")

    def _save(self):
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(self.model, self._model_path())

    def _load(self):
        path = self._model_path()
        if os.path.exists(path):
            self.model = joblib.load(path)
            log.info(f"Loaded ML model from {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["train"], default="train")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    if args.mode == "train":
        m = MLModel(args.symbol)
        m.train(days=args.days)
