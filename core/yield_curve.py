import numpy as np
import pandas as pd

from .utils import CURVE_PATH

CSV_PATH = CURVE_PATH


def load_svensson_params(csv_path, curve_id):
    # Kept for compatibility with callers that select a curve by identifier.
    _ = curve_id
    df = pd.read_parquet(csv_path)
    params = df.set_index("PARAMETER")["VALUE"]
    return params[["BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2"]].to_numpy(dtype=float)


def svensson_yield(t, beta0, beta1, beta2, beta3, tau1, tau2):
    term1 = (1 - np.exp(-t / tau1)) / (t / tau1)
    term2 = term1 - np.exp(-t / tau1)
    term3 = (1 - np.exp(-t / tau2)) / (t / tau2) - np.exp(-t / tau2)
    return beta0 + beta1 * term1 + beta2 * term2 + beta3 * term3


curves = {
    "AAA": "YC.B.U2.EUR.4F.G_N_A.SV_C_YM",
    "All bonds": "YC.B.U2.EUR.4F.G_N_C.SV_C_YM",
}


if __name__ == "__main__":
    query_maturities = np.array([5.5, 8, 20.2])
    params = load_svensson_params(CSV_PATH, curves["All bonds"])
    rates = svensson_yield(query_maturities, *params)

    for t, y in zip(query_maturities, rates):
        print(f"{t} years: {y:.4f}%")
