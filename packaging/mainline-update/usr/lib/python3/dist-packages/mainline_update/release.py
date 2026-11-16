#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yzddmr6
"""Release lookup shared by the notification service and updater."""
import http.client
import re
import subprocess
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_version(version):
    return (bool(re.fullmatch(r'[0-9][0-9A-Za-z.+~\-]{0,127}', version)) and
            subprocess.run(['dpkg', '--validate-version', version],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)


def latest(repository):
    """Resolve releases/latest without fetching a page or sending cookies."""
    request = urllib.request.Request(repository + '/releases/latest',
                                     headers={'User-Agent': 'Mainline-Release-Notifier'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15):
            return None
    except urllib.error.HTTPError as response:
        with response:
            if response.code != 302:
                return None
            location = response.headers.get('Location', '')
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        return None
    prefix = repository + '/releases/tag/'
    if not location.startswith(prefix):
        return None
    tag = location[len(prefix):]
    if not re.fullmatch(r'v?[0-9][0-9A-Za-z.+~\-]{0,127}', tag):
        return None
    version = tag.removeprefix('v')
    return (version, location) if valid_version(version) else None
