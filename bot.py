from aiohttp import web
import asyncio

async def healthcheck(request):
    return web.Response(text="OK", status=200)

async def static_handler(request):
    return web.Response(text="<h1>Сервер работает!</h1>", content_type="text/html", status=200)

async def main():
    app = web.Application()
    app.router.add_get('/health', healthcheck)
    app.router.add_get('/static/index.html', static_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)  # ВАЖНО: 0.0.0.0 и 8080
    await site.start()
    
    print("✅ Сервер запущен на порту 8080")
    print("🔗 Откройте: https://giftbot-production-6040.up.railway.app/static/index.html")
    
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())