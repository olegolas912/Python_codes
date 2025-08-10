# -*- coding: utf-8 -*-
"""
Сводит «raport восточный уренгой.xlsx» + «шахмат.xlsx» → output_final5.xlsx
Добавлено:
- (3) Жёсткая нормализация названий М/ЛУ для сравнения.
- (4) Поддержка дополнительных параметров из «шахмат.xlsx»: Pпл как последнее значение.
- (5) Кэширование структуры месячного блока в шахматах (ускорение) + отладочный режим.
- Обработка ошибок/предупреждений.
"""

from __future__ import annotations
import re, calendar, sys
from pathlib import Path
from collections import defaultdict
import pandas as pd, openpyxl

# ---------------------------------------------------------------------
# Конфиг
# ---------------------------------------------------------------------
CFG = {
    # файлы
    "raport":  Path("raport восточный уренгой.xlsx"),
    "shakmat": Path("шахмат.xlsx"),
    "out":     Path("output_final3.xlsx"),

    # фильтры
    "target_field": "уренгойское",
    "target_lic":   "восточно-уренгойское",

    # допустимые секции
    "sections": {
        "газоконденсатные скважины новые (введенные в текущем месяце)",
        "газоконденсатные скважины старые (введенные до текущего месяца)",
        "газоконденсатные скважины старые (введенные до текущего года)",
    },

    # индексы колонок raport (нумерация с 0)
    "cols": {"well":1, "gas":(3,4), "cond":9, "water":12, "days":5},

    # подписи параметров в «шахмат.xlsx» — допускаются синонимы
    "lbl": {
        "pbuf": ["Pбуф"],
        "pzat": ["Pзат"],
        "dsht": ["Dшт"],
        "ppl" : ["Pпл", "Пластовое давление"],   # новый параметр (пункт 4)
    },

    # месяцы
    "mnum": {"январь":1,"февраль":2,"март":3,"апрель":4,"май":5,"июнь":6,
             "июль":7,"август":8,"сентябрь":9,"октябрь":10,"ноябрь":11,"декабрь":12},

    # масштабы единиц (из «тыс.» в «млн» и т. п.)
    "scale": {"gas":0.001, "cond":0.001, "water":0.001},

    # режим отладки
    "debug": True,
}

# ---------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------
def dbg(msg: str):
    if CFG.get("debug"):
        print(msg)

def norm(s):
    return re.sub(r"\s+"," ", str(s).strip().lower().replace("ё","е")) if s is not None else ""

def num(x: object) -> float | None:
    """Распознавание чисел RU/EN: запятая как десятичная, пробелы/NBSP как «тысячные».
    Понимает формы '(1 234,5)', '>1,2', '1.234,56', '1 234.56' и т. п.
    """
    if x is None:
        return None
    if isinstance(x, (int, float)):
        try:
            return float(x)
        except Exception:
            return None

    s = str(x).strip()
    if not s:
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.lstrip("<>")
    s = s.replace("−", "-")                  # U+2212 → '-'
    s = s.replace("\xa0","").replace("\u202f","").replace(" ","")  # все пробелы

    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    else:
        if "." in s:
            parts = s.split(".")
            if len(parts)>1 and all(p.isdigit() and len(p)==3 for p in parts[1:] if p):
                s = "".join(parts)
    s = re.sub(r"[^0-9.\-]", "", s)
    if not re.search(r"\d", s):
        return None
    try:
        return float(s)
    except Exception:
        return None

def dpm(mon_ru, yr):  # days per month
    try:
        return calendar.monthrange(yr, CFG["mnum"][mon_ru])[1]
    except Exception as e:
        raise ValueError(f"Неизвестный месяц: {mon_ru!r}") from e

def next_month_year(mon_ru, yr):
    m = CFG["mnum"][mon_ru]
    m2 = 1 if m == 12 else m + 1
    y2 = yr + 1 if m == 12 else yr
    return f"01.{m2:02d}.{y2}"

def wellstr(x):
    if x is None:
        return None
    if isinstance(x,(int,float)) and float(x).is_integer():
        return str(int(x))
    return re.sub(r"\s+"," ", str(x).strip())

# ---------------------------------------------------------------------
# Разбор «шахмат.xlsx» с кэшированием (пункт 5)
# ---------------------------------------------------------------------
_ROWS_CACHE: dict[str, list] = {}
_BLOCK_CACHE: dict[tuple, dict] = {}

def _prepare_month(sh, mon_ru: str, yr: int):
    """Вернуть структуру месячного блока: rows/hdr/dcols/label_map. Кэшируется."""
    key = (getattr(sh, 'title', str(id(sh))), mon_ru, yr)
    if key in _BLOCK_CACHE:
        return _BLOCK_CACHE[key]

    title = getattr(sh, 'title', str(id(sh)))
    rows = _ROWS_CACHE.get(title)
    if rows is None:
        rows = list(sh.iter_rows(values_only=True))
        _ROWS_CACHE[title] = rows

    current_year = None
    mon = mon_ru.lower()
    header_idx = None
    for i, row in enumerate(rows):
        a = row[0] if len(row) > 0 else None
        b = row[1] if len(row) > 1 else None
        bnum = num(b)
        if bnum is not None and float(bnum).is_integer():
            bn = int(bnum)
            if 1900 <= bn <= 2100:
                current_year = bn
            continue
        if (current_year == yr and isinstance(a, str) and mon in a.lower()
            and isinstance(b, str) and norm(b) == "дата"):
            header_idx = i
            break
    if header_idx is None:
        _BLOCK_CACHE[key] = None
        return None

    subrows = rows[header_idx:]
    hdr = subrows[0]
    dcols = []
    for j, v in enumerate(hdr):
        if isinstance(v,(int,float)) and float(v).is_integer() and 1<=int(v)<=31:
            dcols.append(j)
        elif isinstance(v,str) and re.fullmatch(r"\d{1,2}", v.strip()):
            d = int(v.strip())
            if 1<=d<=31:
                dcols.append(j)

    label_map = {}
    for idx, r in enumerate(subrows[1:], start=1):
        b = r[1] if len(r) > 1 else None
        if b == "Дата":
            break
        if isinstance(b, str):
            label_map.setdefault(norm(b), idx)

    out = {"rows": subrows, "hdr": hdr, "dcols": dcols, "label_map": label_map}
    _BLOCK_CACHE[key] = out
    return out

def _find_label_index(block: dict, labels: list[str] | str):
    if block is None:
        return None
    if isinstance(labels, str):
        labels = [labels]
    lm = block["label_map"]
    for lab in labels:
        ln = norm(lab)
        for k, idx in lm.items():
            if k.startswith(ln):
                return idx
    return None

def last_daily(sh, mon_ru, yr, labels):
    block = _prepare_month(sh, mon_ru, yr)
    if not block:
        return None
    dcols = block["dcols"]
    if not dcols:
        return None
    ri = _find_label_index(block, labels)
    if ri is None:
        return None
    r = block["rows"][ri]
    for k in reversed(dcols):
        if k < len(r):
            v = num(r[k])
            if v is not None:
                return float(v)
    return None

def avg_daily(sh, mon_ru, yr, labels):
    block = _prepare_month(sh, mon_ru, yr)
    if not block:
        return None
    hdr = block["hdr"]
    ri = _find_label_index(block, labels)
    if ri is None:
        return None
    for j, v in enumerate(hdr):
        if isinstance(v, str) and norm(v) == "среднее":
            vv = num(block["rows"][ri][j])
            if vv is not None:
                return float(vv)
    vals = []
    for k in block["dcols"]:
        row = block["rows"][ri]
        if k < len(row):
            vk = num(row[k])
            if vk is not None:
                vals.append(vk)
    return float(sum(vals)/len(vals)) if vals else None

# ---------------------------------------------------------------------
# Основной проход
# ---------------------------------------------------------------------
def run():
    # открытие файлов
    try:
        xls = pd.ExcelFile(CFG["raport"])
    except Exception as e:
        raise SystemExit(f"Не удалось открыть файл отчёта: {CFG['raport']} — {e}")
    try:
        wb_sh = openpyxl.load_workbook(CFG["shakmat"], data_only=True, read_only=True)
    except Exception as e:
        raise SystemExit(f"Не удалось открыть шахматы: {CFG['shakmat']} — {e}")

    sh_map = {re.sub(r"\s+","", s).lower(): s for s in wb_sh.sheetnames}

    rows = []
    allowed = {norm(s) for s in CFG["sections"]}
    tf, tl = norm(CFG["target_field"]), norm(CFG["target_lic"])
    c = CFG["cols"]; sc = CFG["scale"]

    total_sheets = 0
    for sname in xls.sheet_names:
        parts = sname.split()
        if len(parts) != 2 or norm(parts[0]) not in CFG["mnum"]:
            dbg(f"Пропускаю лист: {sname}")
            continue
        mon, yr = norm(parts[0]), int(parts[1])
        try:
            nday = dpm(mon, yr)
        except Exception:
            dbg(f"Не распознан месяц в листе: {sname}")
            continue
        df = pd.read_excel(CFG["raport"], sheet_name=sname, header=None, dtype=object, engine="openpyxl")
        total_sheets += 1

        agg = defaultdict(lambda: dict(g=0.0, k=0.0, w=0.0, d=0.0, n=0))
        in_sec = False
        cur_f = cur_l = None

        for _, r in df.iterrows():
            first = r.iloc[0]
            if isinstance(first, str):
                t = norm(first)
                if t.startswith("месторождение"):
                    cur_f = t.split(":", 1)[1].strip(); in_sec = False
                    dbg(f"Месторождение → {cur_f}")
                elif t.startswith("лицензионный участок"):
                    cur_l = t.split(":", 1)[1].strip(); in_sec = False
                    dbg(f"ЛУ → {cur_l}")
                else:
                    in_sec = t in allowed
                    if in_sec:
                        dbg(f"Секция включена: {t}")
                continue

            if not in_sec or (tf and cur_f != tf) or (tl and cur_l != tl):
                continue

            wll = wellstr(r.iloc[c["well"]])
            if not wll:
                dbg("Пропущена строка без номера скважины")
                continue

            gas  = sum(num(r.iloc[i]) or 0.0 for i in c["gas"])
            cond = num(r.iloc[c["cond"]])  or 0.0
            wat  = num(r.iloc[c["water"]]) or 0.0
            dys  = num(r.iloc[c["days"]])  or 0.0

            if gas < 0 or cond < 0 or wat < 0 or dys < 0:
                dbg(f"Предупреждение: отрицательные значения у {wll} на листе {sname}")

            agg[wll]["g"] += gas
            agg[wll]["k"] += cond
            agg[wll]["w"] += wat
            agg[wll]["d"] += dys
            agg[wll]["n"] += 1

        # добираем давления/штуцер из «шахмат.xlsx»
        for wll, p in agg.items():
            # ИСПРАВЛЕНО: коэффициент эксплуатации = (сумма_дней) / (кол-во_строк * дней_в_месяце)
            coef = (p["d"]/(p["n"]*nday)) if p["n"] > 0 and nday > 0 else None
            if coef is not None and not (0 <= coef <= 1.2):
                dbg(f"Подозрительный коэффициент эксплуатации {coef:.3f} у {wll} ({sname})")

            plast = zab = ust = ds = None
            key = re.sub(r"\s+", "", wll).lower()
            if key in sh_map:
                sh = wb_sh[sh_map[key]]
                ust   = last_daily(sh, mon, yr, CFG["lbl"]["pbuf"])
                zab   = last_daily(sh, mon, yr, CFG["lbl"]["pzat"])
                ds    = avg_daily (sh, mon, yr, CFG["lbl"]["dsht"])
                plast = last_daily(sh, mon, yr, CFG["lbl"].get("ppl", []))
            else:
                dbg(f"Нет листа в шахматах для скважины: {wll}")

            rows.append({
                "Скважина": wll,
                "Дата": next_month_year(mon, yr),
                "Коэффициент эксплуатации, д.ед.": round(coef, 9) if coef is not None else None,
                "Добыча газа, млн. м3": round(p["g"]*sc["gas"], 6),
                "Добыча конденсата, тыс. т": round(p["k"]*sc["cond"], 6),
                "Добыча воды, тыс. т": round(p["w"]*sc["water"], 6),
                "Пластовое давление, МПа": plast,
                "Забойное давление, МПа": zab,
                "Устьевое давление, МПа": ust,
                "Диаметр штуцера, мм": ds,
            })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["Скважина", "Дата"])
    out.to_excel(CFG["out"], index=False)
    dbg(f"Итог: листов обработано {total_sheets}, строк в выдаче {len(out)}")
    return len(out), str(CFG["out"])

if __name__ == "__main__":
    n, path = run()
    print(f"Готово → {path} ({n} строк)")
