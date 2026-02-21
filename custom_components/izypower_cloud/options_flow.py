import asyncio
import logging
import voluptuous as vol
from homeassistant import config_entries
from .client import IzyClient

_LOGGER = logging.getLogger(__name__)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        # store as a private attribute to avoid clobbering the base property
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Validate refresh period
            refresh_period = user_input.get("refresh_period")
            if refresh_period is not None and refresh_period < 3:
                errors["refresh_period"] = "refresh_period_too_low"
            
            # Check if credentials have changed and validate them
            username = user_input.get("username")
            password = user_input.get("password")
            old_username = self._config_entry.data.get("username", "")
            old_password = self._config_entry.data.get("password", "")
            
            # Validate credentials if they've changed
            if username != old_username or password != old_password:
                _LOGGER.debug("Credentials changed, validating for user: %s", username)
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
                # Update config_entry data for username/password if changed
                updated_data = dict(self._config_entry.data)
                if "username" in user_input:
                    updated_data["username"] = user_input["username"]
                if "password" in user_input:
                    updated_data["password"] = user_input["password"]
                # Use hass.config_entries.async_update_entry to update config_entry data
                self.hass.config_entries.async_update_entry(self._config_entry, data=updated_data)
                
                # Reload the integration to refresh the token with new credentials
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                
                return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options or {}
        # Prefer already-saved options, fall back to initial config data, then to default (3 minutes)
        default_refresh = current.get("refresh_period", self._config_entry.data.get("refresh_period", 3))
        default_username = self._config_entry.data.get("username", "")
        default_password = self._config_entry.data.get("password", "")

        data_schema = vol.Schema(
            {
                vol.Optional("username", default=default_username): str,
                vol.Optional("password", default=default_password): str,
                vol.Optional("refresh_period", default=default_refresh): int,
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)
