# -*- coding: utf-8 -*-
"""
Сводит «raport восточный уренгой.xlsx» + «шахмат.xlsx» → output_final5.xlsx
Логика:
- фильтр по месторождению/ЛУ,
- учёт только нужных секций,
- суммирование дебитов/дней по скважине за месяц,
- сдвиг даты на 1-е число следующего месяца,
- из «шахмат.xlsx» тянет Pбуф (последнее значение), Pзат (последнее), Dшт (среднее),
- надёжный парсер чисел для RU-локали (запятая как десятичная, пробел/NBSP как тысячный).
"""

from __future__ import annotations
import re, calendar
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
    "out":     Path("output_final5.xlsx"),

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

    # подписи параметров в «шахмат.xlsx»
    "lbl": {"pbuf":"Pбуф", "pzat":"Pзат", "dsht":"Dшт"},

    # месяцы
    "mnum": {"январь":1,"февраль":2,"март":3,"апрель":4,"май":5,"июнь":6,
             "июль":7,"август":8,"сентябрь":9,"октябрь":10,"ноябрь":11,"декабрь":12},

    # масштабы единиц (из «тыс.» в «млн» и т. п.)
    "scale": {"gas":0.001, "cond":0.001, "water":0.001},
}

# ---------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------
norm = lambda s: re.sub(r"\s+"," ", str(s).strip().lower().replace("ё","е")) if s is not None else ""

def num(x: object) -> float | None:
    """
    RU-локаль: десятичная = ','; разделитель тысяч = пробел/NBSP.
    Понимает обычный пробел, NBSP (U+00A0) и узкий NBSP (U+202F).
    Точки считаются мусором (и удаляются), если в записи есть запятая.
    Если запятой нет, точка может быть либо десятичной, либо «тысячной» (если группы по 3).
    """
    import re
    if x is None:
        return None
    # уже число
    if isinstance(x, (int, float)):
        try:
            return float(x)
        except Exception:
            return None

    s = str(x).strip()
    if not s:
        return None

    # нормализуем минус (U+2212 → ASCII '-')
    s = s.replace("\u2212", "-")

    # убираем все виды пробелов: обычный, NBSP, узкий NBSP
    s = s.replace("\xa0", "").replace("\u202f", "").replace(" ", "")

    # если есть запятая — она точно десятичная
    if "," in s:
        s = s.replace(".", "")       # выбрасываем точки-группировщики
        s = s.replace(",", ".")      # превращаем в стандартный float
    else:
        # запятой нет. Если есть точки — решаем: это группировка или десятичная
        if "." in s:
            parts = s.split(".")
            # если все группы после первой по 3 цифры → это группировка тысяч
            if len(parts) > 1 and all(p.isdigit() and len(p) == 3 for p in parts[1:] if p):
                s = "".join(parts)  # убираем точки-группировщики
            # иначе оставляем как десятичную точку

    # допускаем только цифры, точку и минус
    s = re.sub(r"[^0-9.\-]", "", s)
    if not re.search(r"\d", s):
        return None
    try:
        return float(s)
    except Exception:
        return None

def dpm(mon_ru, yr):  # days per month
    return calendar.monthrange(yr, CFG["mnum"][mon_ru])[1]

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
# Разбор «шахмат.xlsx»
# ---------------------------------------------------------------------
def loc_header(sh, mon_ru: str, yr: int) -> int | None:
    """
    Найти строку начала блока месяца (месяц+год) в листе «шахмат.xlsx».
    Структура: строка с годом в колонке B, следующая строка — месяц (A) и «Дата» (B).
    Возвращает индекс строки (0-based) месяца или None.
    """
    current_year = None
    mon = mon_ru.lower()
    for i, row in enumerate(sh.iter_rows(values_only=True)):
        a, b = row[:2]
        bnum = num(b)
        if bnum is not None and float(bnum).is_integer():
            bn = int(bnum)
            if 1900 <= bn <= 2100:
                current_year = bn
            continue
        if (current_year == yr
            and isinstance(a, str) and mon in a.lower()
            and isinstance(b, str) and norm(b) == "дата"):
            return i
    return None

def day_cols(hdr):
    cols=[]
    for j,v in enumerate(hdr):
        if isinstance(v,(int,float)) and float(v).is_integer() and 1<=int(v)<=31:
            cols.append(j)
        elif isinstance(v,str) and re.fullmatch(r"\d{1,2}", v.strip()):
            d=int(v.strip())
            if 1<=d<=31:
                cols.append(j)
    return cols

def find_row(rows, label_norm):
    for idx,r in enumerate(rows[1:],start=1):
        if r[1]=="Дата":
            break
        if isinstance(r[1],str) and norm(r[1]).startswith(label_norm):
            return idx
    return None

def last_daily(sh, mon_ru, yr, label):
    hi = loc_header(sh, mon_ru, yr)
    if hi is None:
        return None
    rows = list(sh.iter_rows(values_only=True))[hi:]
    hdr = rows[0]
    dcols = day_cols(hdr)
    if not dcols:
        return None
    ri = find_row(rows, norm(label))
    if ri is None:
        return None
    r = rows[ri]
    for k in reversed(dcols):
        if k < len(r):
            v = num(r[k])
            if v is not None:
                return float(v)
    return None

def avg_daily(sh, mon_ru, yr, label):
    hi=loc_header(sh,mon_ru,yr)
    if hi is None:
        return None
    rows=list(sh.iter_rows(values_only=True))[hi:]
    hdr=rows[0]
    ri=find_row(rows, norm(label))
    if ri is None:
        return None
    # если есть колонка "Среднее" — берём её
    for j,v in enumerate(hdr):
        if isinstance(v,str) and norm(v)=="среднее":
            vv=num(rows[ri][j])
            if vv is not None:
                return float(vv)
    # иначе среднее по дням
    vals=[num(rows[ri][k]) for k in day_cols(hdr) if k<len(rows[ri]) and num(rows[ri][k]) is not None]
    return float(sum(vals)/len(vals)) if vals else None

# ---------------------------------------------------------------------
# Основной проход
# ---------------------------------------------------------------------
def run():
    xls = pd.ExcelFile(CFG["raport"])
    wb_sh = openpyxl.load_workbook(CFG["shakmat"], data_only=True, read_only=True)
    sh_map = {re.sub(r"\s+","",s).lower(): s for s in wb_sh.sheetnames}

    rows = []
    allowed = {norm(s) for s in CFG["sections"]}
    tf, tl = norm(CFG["target_field"]), norm(CFG["target_lic"])
    c = CFG["cols"]; sc = CFG["scale"]

    for sname in xls.sheet_names:
        parts = sname.split()
        if len(parts)!=2 or norm(parts[0]) not in CFG["mnum"]:
            continue
        mon, yr = norm(parts[0]), int(parts[1])
        nday = dpm(mon, yr)
        df = pd.read_excel(CFG["raport"], sheet_name=sname, header=None, dtype=object, engine="openpyxl")

        agg = defaultdict(lambda: dict(g=0.0,k=0.0,w=0.0,d=0.0,n=0))
        in_sec=False; cur_f=cur_l=None

        for _, r in df.iterrows():
            first = r.iloc[0]
            if isinstance(first,str):
                t = norm(first)
                if t.startswith("месторождение"):
                    cur_f = t.split(":",1)[1].strip(); in_sec=False
                elif t.startswith("лицензионный участок"):
                    cur_l = t.split(":",1)[1].strip(); in_sec=False
                else:
                    in_sec = t in allowed
                continue

            if not in_sec or (tf and cur_f!=tf) or (tl and cur_l!=tl):
                continue

            wll = wellstr(r.iloc[c["well"]])
            if not wll:
                continue

            gas  = sum(num(r.iloc[i]) or 0.0 for i in c["gas"])
            cond = num(r.iloc[c["cond"]])  or 0.0
            wat  = num(r.iloc[c["water"]]) or 0.0
            dys  = num(r.iloc[c["days"]])  or 0.0

            agg[wll]["g"] += gas
            agg[wll]["k"] += cond
            agg[wll]["w"] += wat
            agg[wll]["d"] += dys
            agg[wll]["n"] += 1

        # добираем давления/штуцер из «шахмат.xlsx»
        for wll, p in agg.items():
            coef = (p["d"]/nday) if p["n"]>0 and nday>0 else None
            plast=zab=ust=ds=None
            key = re.sub(r"\s+","", wll).lower()
            if key in sh_map:
                sh = wb_sh[sh_map[key]]
                ust   = last_daily(sh, mon, yr, CFG["lbl"]["pbuf"])
                zab   = last_daily(sh, mon, yr, CFG["lbl"]["pzat"])
                ds    = avg_daily (sh, mon, yr, CFG["lbl"]["dsht"])

            rows.append({
                "Скважина": wll,
                "Дата": next_month_year(mon, yr),
                "Коэффициент эксплуатации, д.ед.": round(coef,9) if coef is not None else None,
                "Добыча газа, млн. м3": round(p["g"]*sc["gas"],6),
                "Добыча конденсата, тыс. т": round(p["k"]*sc["cond"],6),
                "Добыча воды, тыс. т": round(p["w"]*sc["water"],6),
                "Пластовое давление, МПа": plast,
                "Забойное давление, МПа": zab,
                "Устьевое давление, МПа": ust,
                "Диаметр штуцера, мм": ds,
            })

    out = pd.DataFrame(rows).sort_values(["Скважина","Дата"])
    out.to_excel(CFG["out"], index=False)
    return len(out), str(CFG["out"])

if __name__ == "__main__":
    n, path = run()
    print(f"Готово → {path} ({n} строк)")
