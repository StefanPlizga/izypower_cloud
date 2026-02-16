import asyncio
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.components import persistent_notification

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .client import IzyClient

_LOGGER = logging.getLogger(__name__)


class IzypowerCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            username = user_input.get("username")
            password = user_input.get("password")
            # Check for duplicate username (temporarily disabled)
            for entry in self._async_current_entries():
                if entry.data.get("username") == username:
                    errors["username"] = "duplicate_username"
                    break
            if not errors:
                _LOGGER.debug("Validating credentials for user: %s", username)
                client = IzyClient(self.hass, username, password)
                try:
                    await client.async_login()
                    _LOGGER.debug("Credentials validated successfully for user: %s", username)
                except asyncio.TimeoutError:
                    _LOGGER.error("Login timeout for user: %s", username)
                    errors["base"] = "cannot_connect"
                except Exception as exc:
                    _LOGGER.error("Login failed for user %s: %s", username, exc)
                    errors["base"] = "invalid_auth"

            if not errors:
                # Set entry title to DISPLAY_NAME_PREFIX and increment if needed
                from .const import DISPLAY_NAME_PREFIX
                base_title = DISPLAY_NAME_PREFIX
                titles = [entry.title for entry in self._async_current_entries()]
                title = base_title
                idx = 2
                while title in titles:
                    title = f"{base_title} {idx}"
                    idx += 1
                return self.async_create_entry(title=title, data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required("username"): str,
                vol.Required("password"): str,
                vol.Optional("refresh_period", default=int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60)): int,
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_reauth(self, data):
        # Reauthentication when credentials fail at runtime
        self._reauth_entry = data
        return await self._show_reauth_form()

    async def _show_reauth_form(self, user_input=None):
        errors = {}
        if user_input is not None:
            username = user_input.get("username")
            password = user_input.get("password")
            _LOGGER.debug("Reauth: validating credentials for user: %s", username)
            client = IzyClient(self.hass, username, password)
            try:
                await client.async_login()
                _LOGGER.debug("Reauth: credentials validated successfully for user: %s", username)
            except asyncio.TimeoutError:
                _LOGGER.error("Reauth: login timeout for user: %s", username)
                errors["base"] = "cannot_connect"
            except Exception as exc:
                _LOGGER.error("Reauth: login failed for user %s: %s", username, exc)
                errors["base"] = "invalid_auth"

            if not errors:
                # update the existing entry
                entries = list(self._async_current_entries())
                if entries:
                    entry = entries[0]
                    new_data = {**entry.data, "username": username, "password": password}
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
                    # Dismiss the reauth notification
                    persistent_notification.async_dismiss(self.hass, notification_id=f"{DOMAIN}_reauth_{entry.entry_id}")
                return self.async_abort(reason="reauth_successful")

        data_schema = vol.Schema({vol.Required("username"): str, vol.Required("password"): str})
        return self.async_show_form(step_id="reauth_confirm", data_schema=data_schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        from .options_flow import OptionsFlowHandler

        return OptionsFlowHandler(config_entry)
