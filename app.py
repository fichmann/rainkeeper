import asyncio
import contextlib
import json
import os
import base64
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pyrainbird.async_client import create_controller

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('RAINKEEPER_DATA_DIR', ROOT / 'data'))
DATA.mkdir(mode=0o700, exist_ok=True)
CONFIG = DATA / 'settings.json'
HISTORY = DATA / 'history.json'
lock = asyncio.Lock()
state = {'device': None, 'forecast': None, 'last_check': None, 'error': None}


class Settings(BaseModel):
    host: str = ''
    password: str = ''
    location: str = ''
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    enabled: bool = False
    probability: int = Field(60, ge=1, le=100)
    precipitation: float = Field(2.0, gt=0, le=100)
    horizon: int = Field(24, ge=1, le=48)


def load_settings():
    return Settings(**json.loads(CONFIG.read_text())) if CONFIG.exists() else Settings()


settings = load_settings()


def persist(path, value):
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f)
    temp.replace(path)


def record(message, kind='info'):
    items = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    items.insert(0, {'time': datetime.now(timezone.utc).isoformat(), 'message': message, 'kind': kind})
    persist(HISTORY, items[:200])


@contextlib.asynccontextmanager
async def device():
    if not settings.password:
        raise ValueError('Enter the device password in settings.')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        async with asyncio.timeout(45):
            controller = await create_controller(session, settings.host, settings.password)
            yield controller


async def read_device():
    async with device() as c:
        delay = await c.get_rain_delay()
        zones = await c.get_zone_states()
        sensor = await c.get_rain_sensor_state()
        return {'delay': delay, 'zones': str(zones), 'sensor': sensor,
                'updated': datetime.now(timezone.utc).isoformat()}


async def forecast():
    if settings.latitude is None or settings.longitude is None:
        raise ValueError('Save a forecast location before running automation.')
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://api.open-meteo.com/v1/forecast', params={
            'latitude': settings.latitude, 'longitude': settings.longitude,
            'hourly': 'precipitation,precipitation_probability', 'forecast_days': 4,
            'timeformat': 'unixtime', 'timezone': 'UTC'})
        r.raise_for_status()
        hourly = r.json()['hourly']
    now = datetime.now(timezone.utc).timestamp()
    indexes = [i for i, t in enumerate(hourly['time']) if now - 3600 < t <= now + settings.horizon * 3600]
    if len(indexes) < settings.horizon:
        raise ValueError('Forecast is incomplete; no device changes made.')
    amounts = [hourly['precipitation'][i] for i in indexes]
    chances = [hourly['precipitation_probability'][i] for i in indexes]
    if any(x is None for x in amounts + chances):
        raise ValueError('Forecast contains missing values; no device changes made.')
    total, chance = round(sum(amounts), 2), max(chances)
    return {'mm': total, 'probability': chance,
            'rain': total >= settings.precipitation or chance >= settings.probability,
            'hours': [{'time': hourly['time'][i], 'mm': hourly['precipitation'][i],
                       'probability': hourly['precipitation_probability'][i]} for i in indexes]}


async def check(apply=True):
    async with lock:
        try:
            f = await forecast()
            state['forecast'] = f
            d = await read_device()
            state['device'] = d
            message = f"Next {settings.horizon}h: {f['mm']} mm, {f['probability']}% peak rain chance. "
            if f['rain'] and apply and settings.enabled:
                if d['delay'] < 1:
                    async with device() as c:
                        await c.set_rain_delay(1)
                        if await c.get_rain_delay() < 1:
                            raise ValueError('Controller did not confirm the rain delay.')
                    state['device']['delay'] = 1
                    message += 'Applied and verified a one-day rain delay.'
                else:
                    message += 'Existing rain delay preserved.'
            elif f['rain']:
                message += 'Rain delay recommended; automation is off.'
            else:
                message += 'No new delay needed. Existing delays expire on the controller.'
            state['error'] = None
            record(message)
        except Exception as e:
            state['error'] = f'{type(e).__name__}: {e}'
            record(state['error'], 'error')
        state['last_check'] = datetime.now(timezone.utc).isoformat()
        return state


async def scheduler():
    while True:
        if settings.enabled:
            await check()
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(lifespan=lifespan)


@app.middleware('http')
async def protect(request: Request, call_next):
    # Same-origin writes and loopback Host prevent cross-site requests and DNS rebinding.
    allowed = os.environ.get('RAINKEEPER_ALLOWED_HOSTS', '127.0.0.1:8080,localhost:8080,testserver').split(',')
    if request.headers.get('host') not in allowed:
        return JSONResponse({'detail': 'Invalid host'}, 403)
    auth_file = os.environ.get('RAINKEEPER_AUTH_FILE')
    if auth_file and request.url.path != '/healthz':
        auth = json.loads(Path(auth_file).read_text())
        try:
            scheme, encoded = request.headers.get('authorization', '').split(' ', 1)
            user, password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            valid = scheme.lower() == 'basic' and secrets.compare_digest(user, auth['username']) and secrets.compare_digest(password, auth['password'])
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            return JSONResponse({'detail': 'Sign in required'}, 401, headers={'WWW-Authenticate': 'Basic realm="Rainkeeper"'})
    if request.method == 'POST' and request.headers.get('x-rainbird-client') != 'dashboard':
        return JSONResponse({'detail': 'Invalid client'}, 403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/healthz')
async def health():
    return {'ok': True}


@app.get('/api/status')
async def status():
    public = settings.model_dump(exclude={'password'})
    return {'settings': public, 'password_set': bool(settings.password), **state,
            'history': json.loads(HISTORY.read_text()) if HISTORY.exists() else []}


@app.post('/api/settings')
async def save(new: Settings):
    global settings
    async with lock:
        if not new.password:
            new.password = settings.password
        if new.location != settings.location or new.latitude is None or new.longitude is None:
            async with httpx.AsyncClient(timeout=20) as client:
                # US ZIP resolution avoids geocoder ambiguity between postal codes and city names.
                if new.location.isdigit() and len(new.location) == 5:
                    r = await client.get(f'https://api.zippopotam.us/us/{new.location}')
                    if r.status_code != 200:
                        raise HTTPException(400, 'ZIP code could not be resolved.')
                    place = r.json()['places'][0]
                    new.latitude, new.longitude = float(place['latitude']), float(place['longitude'])
                else:
                    raise HTTPException(400, 'Enter a US ZIP code, or provide coordinates.')
        persist(CONFIG, new.model_dump())
        settings = new
        state['forecast'] = None
        record('Settings saved. Automation ' + ('enabled.' if settings.enabled else 'disabled.'))
    return {'ok': True}


@app.post('/api/check')
async def run_check():
    return await check()


@app.post('/api/connect')
async def connect():
    async with lock:
        try:
            state['device'] = await read_device()
            state['error'] = None
        except Exception as e:
            state['device'] = None
            state['error'] = f'{type(e).__name__}: {e}'
    return state


class Delay(BaseModel):
    days: int = Field(ge=0, le=14)


@app.post('/api/delay')
async def delay(body: Delay):
    async with lock:
        try:
            async with device() as c:
                await c.set_rain_delay(body.days)
                actual = await c.get_rain_delay()
                if actual != body.days:
                    raise ValueError('Rain delay verification failed.')
            state['device'] = await read_device()
            record(f'Manual rain delay set to {body.days} day(s).')
            return {'ok': True}
        except Exception as e:
            raise HTTPException(502, f'{type(e).__name__}: {e}') from e


@app.get('/')
async def index():
    return FileResponse(ROOT / 'static/index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
