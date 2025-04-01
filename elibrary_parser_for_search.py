import time
import re
import csv
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def open_elibrary_and_search(query):
    """
    Открывает elibrary.ru, вводит поисковый запрос,
    нажимает Enter для поиска и дожидается первой страницы результатов.
    Возвращает объект WebDriver, уже стоящий на странице результатов #1.
    """

    chrome_options = Options()
    chrome_options.add_argument("--headless")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.get("https://elibrary.ru")

    # Ожидаем поле ввода с name='ftext'
    search_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "ftext"))
    )
    search_input.send_keys(query)
    search_input.send_keys(Keys.ENTER)

    # Ожидаем появления таблицы с id="restab" (первая страница результатов)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "restab"))
    )

    time.sleep(1)  # небольшая задержка
    return driver

def parse_current_page(driver):
    """
    Извлекает данные о статьях с текущей страницы, возвращает список словарей.
    Каждая статья: title, authors, year, link
    """
    soup = BeautifulSoup(driver.page_source, "html.parser")
    table = soup.find("table", id="restab")
    articles = []
    if not table:
        return articles

    rows = table.find_all("tr", id=True)
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        cell = tds[1]

        # Название и ссылка
        link_tag = cell.find("a", href=True)
        if not link_tag:
            continue
        title = link_tag.get_text(strip=True)
        link = link_tag["href"]
        if link.startswith("/"):
            link = "https://elibrary.ru" + link

        # Авторы
        authors_tag = cell.find("i")
        authors = authors_tag.get_text(strip=True) if authors_tag else ""

        # Год (первая 4-значная встреча)
        text_block = cell.get_text(" ", strip=True)
        year_match = re.search(r"\b(19|20)\d{2}\b", text_block)
        year = year_match.group(0) if year_match else ""

        articles.append({
            "title": title,
            "authors": authors,
            "year": year,
            "link": link
        })
    return articles

def go_to_next_page(driver):
    """
    Ищет ссылку или кнопку "Следующая страница" и кликает по ней,
    чтобы перейти на следующую страницу. Если удачно перешли, вернёт True,
    иначе (ссылки нет) вернёт False.
    """
    try:
        # Жмем на >> для перехода на новую страницу:
        next_link = driver.find_element(By.XPATH, "//a[contains(text(), '>>')]")
        next_link.click()

        # Ждем таблицу на новой странице,если не успевает загрузиться увеличить время
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "restab"))
        )
        time.sleep(1)
        return True
    except:
        return False

def scrape_elibrary(query, max_pages=3, headless=False):
    """
    - Открывает elibrary.ru, вводит query, жмет Enter.
    - Парсит результаты с нескольких страниц (до max_pages или пока не закончится).
    - Возвращает список словарей: title, authors, year, link
    """
    driver = open_elibrary_and_search(query, headless=headless)
    all_articles = []

    try:
        for page_num in range(1, max_pages+1):
            # Парсим текущую страницу
            page_data = parse_current_page(driver)
            all_articles.extend(page_data)
            print(f"Страница {page_num}, найдено статей: {len(page_data)}")

            # Пытаемся перейти к следующей странице
            ok = go_to_next_page(driver)
            if not ok:
                print("Следующей страницы нет, останавливаемся.")
                break
    finally:
        driver.quit()

    return all_articles

if __name__ == "__main__":
    user_query = str(input("Введите запрос для поиска: "))
    max_pages = 3  # Сколько страниц парсим
    print(f"Поиск по запросу: {user_query}, максимум страниц: {max_pages}")

    articles = scrape_elibrary(user_query, max_pages=max_pages, headless=False)
    print(f"Всего собрано статей: {len(articles)}")

    # Сохраняем в CSV с разделителем ;
    with open("articles_semicolon.csv", "w", newline="", encoding="utf-8") as f:
        fieldnames = ["title","authors","year","link"]
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        for a in articles:
            writer.writerow(a)
    
    print("Данные сохранены в 'articles_semicolon.csv' с разделителем ';'")
