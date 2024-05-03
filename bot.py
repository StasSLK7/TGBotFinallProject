import telebot

from validators import *  # модуль для валидации
from speechKit import *
from yandex_gpt import ask_gpt  # модуль для работы с GPT
from config import TOKEN, LOGS, COUNT_LAST_MSG
# подтягиваем функции из database файла
from database import create_database, add_message, select_n_last_messages

bot = telebot.TeleBot(TOKEN)


@bot.message_handler(content_types=['voice'])
def handle_voice(message: telebot.types.Message):
    try:
        user_id = message.from_user.id

        # Проверка на максимальное количество пользователей
        status_check_users, error_message = check_number_of_users(user_id)
        if not status_check_users:
            bot.send_message(user_id, error_message)
            return

        # Проверка на доступность аудиоблоков
        stt_blocks, error_message = is_stt_block_limit(user_id, message.voice.duration)  # !!!
        if error_message:
            bot.send_message(user_id, error_message)
            return

        # Обработка голосового сообщения
        file_id = message.voice.file_id
        file_info = bot.get_file(file_id)
        file = bot.download_file(file_info.file_path)
        status_stt, stt_text = speech_to_text(file)
        if not status_stt:
            bot.send_message(user_id, stt_text)
            return

        # Запись в БД
        add_message(user_id=user_id, full_message=[stt_text, 'user', 0, 0, stt_blocks])

        # Проверка на доступность GPT-токенов
        last_messages, total_spent_tokens = select_n_last_messages(user_id, COUNT_LAST_MSG)
        total_gpt_tokens, error_message = is_gpt_token_limit(last_messages, total_spent_tokens)
        if error_message:
            bot.send_message(user_id, error_message)
            return

        # Запрос к GPT и обработка ответа
        status_gpt, answer_gpt, tokens_in_answer = ask_gpt(last_messages)
        if not status_gpt:
            bot.send_message(user_id, answer_gpt)
            return
        total_gpt_tokens += tokens_in_answer

        # Проверка на лимит символов для SpeechKit
        tts_symbols, error_message = is_tts_symbol_limit(user_id, answer_gpt)

        # Запись ответа GPT в БД
        add_message(user_id=user_id, full_message=[answer_gpt, 'assistant', total_gpt_tokens, tts_symbols, 0])

        if error_message:
            bot.send_message(user_id, error_message)
            return

        # Преобразование ответа в аудио и отправка
        status_tts, voice_response = text_to_speech(answer_gpt)
        if status_tts:
            bot.send_voice(user_id, voice_response, reply_to_message_id=message.id)
        else:
            bot.send_message(user_id, answer_gpt, reply_to_message_id=message.id)

    except Exception as e:
        logging.error(e)
        bot.send_message(user_id, "Не получилось ответить. Попробуй записать другое сообщение")


@bot.message_handler(commands=['start'])
def tts_handler(message):
    user_id = message.from_user.id
    bot.send_message(user_id, 'Я - бот-gpt, голосовой помощник\n'
                              'Выполни комманду /tts для того, чтобы озвучить текст.\n'
                              'Выполнит команду /stt для того, чтобы отправить аудио и преобразовать его в текст\n'
                              'Или напиши свой вопрос или задай его в голосовом сообщении'
                     )


@bot.message_handler(commands=['tts'])
def tts_handler(message):
    user_id = message.from_user.id
    bot.send_message(user_id, 'Отправь следующим сообщеним текст, чтобы я его озвучил!')
    bot.register_next_step_handler(message, tts)


def tts(message):
    user_id = message.from_user.id
    text = message.text
    # Проверка, что сообщение действительно текстовое
    if message.content_type != 'text':
        bot.send_message(user_id, 'Отправь текстовое сообщение')
        return

    # Считаем символы в тексте и проверяем сумму потраченных символов
    text_symbol = is_tts_symbol_limit(message, text)
    tts_symbols, error_message = is_tts_symbol_limit(user_id, text)
    if error_message:
        bot.send_message(user_id, error_message)
        return

    add_message(user_id=user_id, full_message=[text, 'check', 0, tts_symbols, 0])

    # Получаем статус и содержимое ответа от SpeechKit
    status, content = text_to_speech(text)

    # Если статус True - отправляем голосовое сообщение, иначе - сообщение об ошибке
    try:
        if status:
            bot.send_voice(user_id, content, reply_to_message_id=message.id)
        else:
            bot.send_message(user_id, content, reply_to_message_id=message.id)
    except:
        bot.send_message(user_id, "Что-то пошло не так. Возможно, ты запретил отправлять аудио")


# обрабатываем текстовые сообщения
@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        user_id = message.from_user.id

        # ВАЛИДАЦИЯ: проверяем, есть ли место для ещё одного пользователя (если пользователь новый)
        status_check_users, error_message = check_number_of_users(user_id)
        if not status_check_users:
            bot.send_message(user_id, error_message)  # мест нет =(
            return

        # БД: добавляем сообщение пользователя и его роль в базу данных
        full_user_message = [message.text, 'user', 0, 0, 0]
        add_message(user_id=user_id, full_message=full_user_message)

        # ВАЛИДАЦИЯ: считаем количество доступных пользователю GPT-токенов
        # получаем последние 4 (COUNT_LAST_MSG) сообщения и количество уже потраченных токенов
        last_messages, total_spent_tokens = select_n_last_messages(user_id, COUNT_LAST_MSG)
        # получаем сумму уже потраченных токенов + токенов в новом сообщении и оставшиеся лимиты пользователя
        total_gpt_tokens, error_message = is_gpt_token_limit(last_messages, total_spent_tokens)
        if error_message:
            # если что-то пошло не так — уведомляем пользователя и прекращаем выполнение функции
            bot.send_message(user_id, error_message)
            return

        # GPT: отправляем запрос к GPT
        status_gpt, answer_gpt, tokens_in_answer = ask_gpt(last_messages)
        # GPT: обрабатываем ответ от GPT
        if not status_gpt:
            # если что-то пошло не так — уведомляем пользователя и прекращаем выполнение функции
            bot.send_message(user_id, answer_gpt)
            return
        # сумма всех потраченных токенов + токены в ответе GPT
        total_gpt_tokens += tokens_in_answer

        # БД: добавляем ответ GPT и потраченные токены в базу данных
        full_gpt_message = [answer_gpt, 'assistant', total_gpt_tokens, 0, 0]
        add_message(user_id=user_id, full_message=full_gpt_message)

        bot.send_message(user_id, answer_gpt, reply_to_message_id=message.id)  # отвечаем пользователю текстом
    except Exception as e:
        print(e)
        logging.error(e)  # если ошибка — записываем её в логи
        bot.send_message(message.from_user.id, "Не получилось ответить. Попробуй написать другое сообщение")


@bot.message_handler(content_types=['voice'])
def handle_voice(message: telebot.types.Message):
    try:
        user_id = message.from_user.id  # Идентификатор пользователя, который отправил сообщение

        # Получение информации о голосовом файле и его загрузка
        file_id = message.voice.file_id  # Идентификатор голосового файла в сообщении
        file_info = bot.get_file(file_id)  # Получение информации о файле для загрузки
        file = bot.download_file(file_info.file_path)  # Загрузка файла по указанному пути

        # Преобразование голосового сообщения в текст с помощью SpeechKit
        status_stt, stt_text = speech_to_text(file)  # Обращение к функции speech_to_text для получения текста
        if not status_stt:
            # Отправка сообщения об ошибке, если преобразование не удалось
            bot.send_message(user_id, stt_text)
            return

            # Отправка нескольких последних сообщений от пользователя в GPT для генерации ответа
            # В константе COUNT_LAST_MSG хранится количество сообщений пользователя, которые передаем
            last_messages, total_spent_tokens = select_n_last_messages(user_id, COUNT_LAST_MSG)
            status_gpt, answer_gpt = ask_gpt(last_messages)  # Обращение к GPT с запросом
            if not status_gpt:
                # Отправка сообщения об ошибке, если GPT не смог сгенерировать ответ
                bot.send_message(user_id, answer_gpt)
                return

        # Преобразование текстового ответа от GPT в голосовое сообщение
        status_tts, voice_response = text_to_speech(
            answer_gpt)  # Обращение к функции text_to_speech для получения аудио
        if not status_tts:
            # Отправка текстового ответа GPT, если преобразование в аудио не удалось
            bot.send_message(user_id, answer_gpt, reply_to_message_id=message.id)
        else:
            # Отправка голосового сообщения, если преобразование в аудио прошло успешно
            bot.send_voice(user_id, voice_response, reply_to_message_id=message.id)
    except Exception as e:
        print(e)
        # Логирование ошибки
        logging.error(e)
        # Уведомление пользователя о непредвиденной ошибке
        bot.send_message(user_id, "Не получилось ответить. Попробуй записать другое сообщение")


bot.polling()
