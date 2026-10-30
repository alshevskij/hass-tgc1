<p align="center">
  <img src="https://raw.githubusercontent.com/alshevskij/hass-tgc1/main/images/logo_tgc.png" alt="Логотип ТГК-1" width="200">
</p>
# Личный кабинет ПАО «ТГК-1» в твоём Home Assistant!

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/alshevskij/hass-tgc1?include_prereleases&sort=semver)](https://github.com/alshevskij/hass-tgc1/releases)
[![License](https://img.shields.io/github/license/alshevskij/hass-tgc1)](https://github.com/alshevskij/hass-tgc1/blob/main/LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-blue)](https://www.home-assistant.io/)

Неофициальная интеграция для [Home Assistant](https://www.home-assistant.io/), которая позволяет получать данные из личного кабинета [lk.tgc1.ru](https://lk.tgc1.ru/) (ПАО «ТГК-1»).

## Возможности интеграции:

*   **Сенсоры счётчиков:** текущие показания, дата последней передачи показаний, дата следующей поверки счётчиков.
*   **Сенсор задолженности** по лицевому счёту.
*   **Передача показаний:** через сервис `tgc1.submit_reading` или через поле ввода и кнопку в карточке устройства интеграции.
*   **Квитанции:** сенсоры с датой и ID последней квитанции, а также кнопка для скачивания PDF-файла квитанции в папку `/media/tgc1/` (доступна по локальному URL).
*   **Поддержка нескольких счётчиков:** если в вашем ЛК несколько счётчиков (например, ГВС и отопление), интеграция автоматически создаст отдельные сенсоры и кнопки для каждого из них.

> **Статус тестирования:** Интеграция протестирована только на счётчиках ГВС (горячая вода). Если у вас другие типы счётчиков и вы обнаружили ошибки — создайте Issue. ✨

## Установка:

### Через HACS (рекомендуется)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alshevskij&repository=hass-tgc1&category=integration)

1. Убедитесь, что у вас установлен [HACS](https://hacs.xyz/).
2. В HACS перейдите в раздел **Интеграции**.
3. Нажмите на три точки в правом верхнем углу и выберите **Custom repositories**.
4. Вставьте URL этого репозитория: `https://github.com/alshevskij/hass-tgc1`.
5. Выберите категорию **Integration** и нажмите **Add**.
6. Найдите интеграцию **ТГК-1** в списке и установите её.
7. Перезапустите Home Assistant.

### Вручную:

1. Скачайте папку `custom_components/tgc1` из этого репозитория.
2. Скопируйте её в папку `custom_components` вашего Home Assistant (обычно `/config/custom_components/`).
3. Перезапустите Home Assistant.

## Настройка:

1. Перейдите в **Настройки → Устройства и службы**.
2. Нажмите **Добавить интеграцию** и выберите **ТГК-1: Личный кабинет**.
3. Введите e-mail и пароль от вашего личного кабинета `lk.tgc1.ru`.

> **Примечание для пользователей с несколькими лицевыми счетами:** 
> API ТГК-1 отдаёт общий список квитанций. Поэтому сенсоры квитанций и кнопка их скачивания привязываются к первому лицевому счёту в списке. При этом скачиваются все доступные документы, а сенсоры задолженности создаются отдельно для каждого счёта.

## Сервисы

### Передача показаний (`tgc1.submit_reading`)

Для передачи показаний необходимо знать `entity_id` сенсора нужного счётчика (он заканчивается на «... показание»).

```yaml
action: tgc1.submit_reading
data:
  entity_id: sensor.gvs_m3_pokazanie # Замените на свой сенсор
  value: 197.5
  max_increase: 30 # Необязательно, по умолчанию 30
```
Сервис вернёт отчёт о передаче (предыдущее показание, новое, разницу) и подтверждение из ЛК.

### Скачивание квитанции (`tgc1.download_document`)

Скачивает PDF-файл конкретной квитанции по её ID (ID можно найти в атрибутах сенсора «ID последней квитанции»).

```yaml
action: tgc1.download_document
data:
  document_id: 12345678
```
## Примеры использования

### Уведомление о новой квитанции с отправкой PDF в Telegram

Настройте автоматизацию, которая будет присылать вам файл квитанции, как только он появится в ЛК.

```yaml
alias: "ТГК-1: Отправка квитанции в Telegram"
triggers:
  - trigger: event
    event_type: tgc1_new_document
actions:
  - action: telegram_bot.send_document
    data:
      file: "{{ trigger.event.data.path }}"
      caption: >-
        Новая квитанция ТГК-1 за {{ trigger.event.data.period }}.
        (Дата: {{ trigger.event.data.date }})
    target: "ВАШ_CHAT_ID"
```

### Кнопка для быстрого доступа к квитанциям на дашборде

Добавьте на дашборд кнопку, которая будет открывать папку с вашими PDF-файлами:

```yaml
type: button
name: Квитанции ТГК-1
icon: mdi:file-document-multiple
tap_action:
  action: navigate
  navigation_path: /media-browser/browser # Укажите путь к папке tgc1
```

## Решение проблем

Если интеграция работает некорректно (например, не приходят квитанции или показания), включите отладочные логи.

Для этого добавьте в ваш `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.tgc1: debug
```

После перезапуска Home Assistant логи можно посмотреть в Настройки → Система → Журналы (или в файле `home-assistant.log`).

## Дисклеймер:

⚠️ **ЭТО НЕОФИЦИАЛЬНАЯ ИНТЕГРАЦИЯ.** Она никаким образом не связана с ПАО «ТГК-1» и использует внутренний API личного кабинета, который может измениться в любой момент.

*   Ваш пароль хранится в Home Assistant в зашифрованном виде (средствами самого Home Assistant).
*   Используйте интеграцию на свой страх и риск, так как она писалась исключительно под личные нужды.
*   Я не могу гарантировать стабильную работу интеграции, так как она всё ещё тестируется.
*   Я не несу ответственности за возможные сбои в работе личного кабинета или утечку данных.
