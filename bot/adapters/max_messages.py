class MaxMessageAdapter:
    def __init__(self, event):
        self.event = event

        # имитируем aiogram структуру
        self.from_user = type("User", (), {
            "id": event.user_id,
            "username": None
        })()

        self.text = event.message.body.text

    async def answer(self, text, parse_mode=None):
        await self.event.message.answer(text)

    async def answer_document(self, file):
        # MAX может требовать upload → пока заглушка
        await self.event.message.answer(f"[Файл отправлен]: {file}")

class MaxCallbackAdapter:
    def __init__(self, data, message):
        self.data = data
        self.message = message

    async def answer(self, text):
        await self.message.answer(text)