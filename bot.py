import sys
import asyncio
from aiohttp import web

print("🔴 СКРИПТ НАЧАЛ РАБОТУ", flush=True)

async def healthcheck(request):
    print("🟢 Healthcheck запрошен", flush=True)
    return web.Response(text="OK", status=200)

async def static_handler(request):
    print("🟢 Статика запрошена", flush=True)
    return web.Response(
        text="<h1>🚀 Сервер работает!</h1><p>Если вы видите это сообщение, значит всё настроено правильно.</p>",
        content_type="text/html",
        status=200
    )

async def main():
    print("🔴 ЗАПУСК main()", flush=True)
    
    app = web.Application()
    app.router.add_get('/health', healthcheck)
    app.router.add_get('/static/', static_handler)
    app.router.add_get('/static/index.html', static_handler)
    
    print("🔴 Приложение создано, запускаем сервер...", flush=True)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)
    await site.start()
    
    print("✅ СЕРВЕР ЗАПУЩЕН НА ПОРТУ 8080", flush=True)
    print("🔗 Откройте: https://giftbot-production-6040.up.railway.app/static/index.html", flush=True)
    
    await asyncio.Event().wait()

if __name__ == '__main__':
    print("🔴 Запуск asyncio.run(main())", flush=True)
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
        import traceback
        traceback.print_exc()