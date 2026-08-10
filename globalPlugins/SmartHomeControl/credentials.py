# -*- coding: utf-8 -*-
"""
Smart Home Control - Verschluesselte Passwort-Properties des Plugins
Ausgelagert aus __init__.py (Modul-Aufteilung, Verhalten unverändert).
"""

from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation fehlgeschlagen: {e}")

from .security import encrypt_dpapi, decrypt_dpapi, is_encrypted


class _CredentialsMixin:
    """Passwort-Properties: verschluesselt im Speicher, on-demand entschluesselt."""

    @property
    def password(self):
        """Decrypts the password on demand - never permanently as plain text in memory."""
        if self._encrypted_password:
            return decrypt_dpapi(self._encrypted_password)
        return ""
    
    @password.setter
    def password(self, value):
        """Encrypts the password immediately - plain text is never stored persistently.

        The setter ALWAYS treats values from the UI as plain text and
        encrypts them. Already encrypted values (e.g. from the
        configuration) are set via ``set_encrypted_password``.
        """
        if value:
            self._encrypted_password = encrypt_dpapi(value)
        else:
            self._encrypted_password = ""

    def set_encrypted_password(self, encrypted_value):
        """Sets an already encrypted password string (from the config).

        If the value carries no known encryption prefix, it is treated as
        plain text and migrated on first access via the ``password``
        property.
        """
        if encrypted_value and is_encrypted(encrypted_value):
            self._encrypted_password = encrypted_value
        elif encrypted_value:
            # Legacy plain text from an old version -> encrypt
            self._encrypted_password = encrypt_dpapi(encrypted_value)
        else:
            self._encrypted_password = ""

    @property
    def vesync_password(self):
        """Decrypts the VeSync password on demand."""
        if self._encrypted_vesync_password:
            return decrypt_dpapi(self._encrypted_vesync_password)
        return ""

    @vesync_password.setter
    def vesync_password(self, value):
        """Encrypts the VeSync password immediately (plain text from the UI)."""
        if value:
            self._encrypted_vesync_password = encrypt_dpapi(value)
        else:
            self._encrypted_vesync_password = ""

    def set_encrypted_vesync_password(self, encrypted_value):
        """Sets an already encrypted VeSync password string (from the config)."""
        if encrypted_value and is_encrypted(encrypted_value):
            self._encrypted_vesync_password = encrypted_value
        elif encrypted_value:
            self._encrypted_vesync_password = encrypt_dpapi(encrypted_value)
        else:
            self._encrypted_vesync_password = ""

    @property
    def cozytouch_password(self):
        """Decrypts the Cozytouch password on demand."""
        if self._encrypted_cozytouch_password:
            return decrypt_dpapi(self._encrypted_cozytouch_password)
        return ""

    @cozytouch_password.setter
    def cozytouch_password(self, value):
        """Encrypts the Cozytouch password immediately (plain text from the UI)."""
        if value:
            self._encrypted_cozytouch_password = encrypt_dpapi(value)
        else:
            self._encrypted_cozytouch_password = ""

    def set_encrypted_cozytouch_password(self, encrypted_value):
        """Sets an already encrypted Cozytouch password string (from the config)."""
        if encrypted_value and is_encrypted(encrypted_value):
            self._encrypted_cozytouch_password = encrypted_value
        elif encrypted_value:
            self._encrypted_cozytouch_password = encrypt_dpapi(encrypted_value)
        else:
            self._encrypted_cozytouch_password = ""

