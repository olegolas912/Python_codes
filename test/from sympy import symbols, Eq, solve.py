def find_solutions():
    for x in range(-100, 101):
        for y in range(-100, 101):
            if 3 + 7*x - 9*y == 12:
                print(f"x = {x}, y = {y}")

# Запуск поиска решений
print(find_solutions())
