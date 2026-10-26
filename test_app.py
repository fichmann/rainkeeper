import asyncio
import unittest
import json
import os
import tempfile
import httpx
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
import app

class DashboardSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_access_controls(self):
        with tempfile.NamedTemporaryFile(mode='w') as f:
            json.dump({'username':'test','password':'secret'},f)
            f.flush()
            with patch.dict(os.environ, {'RAINKEEPER_AUTH_FILE':f.name,'RAINKEEPER_ALLOWED_HOSTS':'testserver'}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://testserver') as client:
                    self.assertEqual((await client.get('/api/status')).status_code,401)
                    self.assertEqual((await client.get('/healthz')).status_code,200)
                    self.assertEqual((await client.get('/api/status',auth=('test','secret'))).status_code,200)
                    self.assertEqual((await client.get('/api/status',auth=('test','wrong'))).status_code,401)
                    self.assertEqual((await client.post('/api/check',auth=('test','secret'),json={})).status_code,403)
                    self.assertEqual((await client.get('/healthz',headers={'host':'evil.example'})).status_code,403)

class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old = app.settings
        app.settings = app.Settings(enabled=True)
    async def asyncTearDown(self):
        app.settings = self.old
    async def scenario(self, rain=True, delay=0, failure=False):
        controller = AsyncMock()
        controller.get_rain_delay.return_value = 1
        @asynccontextmanager
        async def device():
            yield controller
        with patch.object(app, 'forecast', AsyncMock(side_effect=ValueError('offline')) if failure else AsyncMock(return_value={'rain':rain,'mm':4,'probability':80})), patch.object(app,'read_device',AsyncMock(return_value={'delay':delay})), patch.object(app,'device',device), patch.object(app,'record'):
            await app.check()
        return controller
    async def test_rain_delays(self):
        c=await self.scenario()
        c.set_rain_delay.assert_awaited_once_with(1)
    async def test_longer_delay_preserved(self):
        c=await self.scenario(delay=4)
        c.set_rain_delay.assert_not_called()
    async def test_dry_never_clears_manual_delay(self):
        c=await self.scenario(rain=False,delay=3)
        c.set_rain_delay.assert_not_called()
    async def test_disabled_never_writes(self):
        app.settings.enabled=False
        c=await self.scenario()
        c.set_rain_delay.assert_not_called()
    async def test_forecast_failure_never_writes(self):
        c=await self.scenario(failure=True)
        c.set_rain_delay.assert_not_called()
        self.assertIn('offline',app.state['error'])

if __name__=='__main__': unittest.main()
