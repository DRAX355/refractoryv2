"""Safe evaluation of numbers / formulas typed by the user (e.g.  W-2*T , 0.15*Rmax )."""
import math

FUNCS = {"pi": math.pi, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
         "asin": math.asin, "acos": math.acos, "atan": math.atan, "rad": math.radians,
         "deg": math.degrees, "min": min, "max": max, "abs": abs, "round": round,
         "ceil": math.ceil, "floor": math.floor}


def evaluate(text, variables=None):
    """Return a float from '12.5', '2*R', 'D/2-T' ... Raises ValueError with a readable message."""
    text = str(text).strip()
    if not text:
        raise ValueError("empty value")
    if "__" in text:
        raise ValueError("not allowed")
    try:
        val = eval(text, {"__builtins__": {}}, {**FUNCS, **(variables or {})})
    except NameError as e:
        raise ValueError(f"unknown name in '{text}' ({e})")
    except Exception as e:
        raise ValueError(f"cannot read '{text}' as a number ({e})")
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"'{text}' is not a number")
    return float(val)
