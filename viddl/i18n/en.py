"""The English words, by key.

This is the catalogue every language falls back to, so it has to be complete:
a key missing here is a bug in the app, not a gap in a translation (U7).

Keys are ``error.*`` for the explanations in :mod:`viddl.errors`. An
``*.hint`` is the second line, which may not exist. ``{...}`` marks a value
filled in at the moment it is said — ``{sign_in}`` is the name the qualities
screen gives its sign-in row, quoted into the advice so that the words on
screen and the words in the message cannot drift apart.
"""

from __future__ import annotations

CODE = "en"
NAME = "English"

MESSAGES = {
    # -- what went wrong, in the order viddl.errors tries the rules -----------
    "error.cookies_read": "Viddl could not read your browser's cookies.",
    "error.cookies_read.hint": (
        "Sign in to the site in that browser first, then close the browser and "
        "try again — an open browser can hold the store locked. A different "
        "browser works too: choose “{sign_in}” on the qualities screen."
    ),
    "error.sign_in": "The site wants you to be signed in to watch this one.",
    "error.sign_in.hint": (
        "Age-restricted, bot-checked and members-only videos answer to a "
        "signed-in session. Choose “{sign_in}” and Viddl will read the cookies "
        "that browser already has."
    ),
    "error.private": "That video is private.",
    "error.private.hint": (
        "Only the owner can download it. Is there a public version you meant to "
        "open?"
    ),
    "error.unavailable": "That video is not available any more.",
    "error.unavailable.hint": (
        "It may have been deleted, made private, or blocked in your region."
    ),
    "error.members": "That video is for channel members only.",
    "error.ffmpeg": "Viddl needs ffmpeg for this download, and it is not installed yet.",
    "error.ffmpeg.hint": (
        "Viddl can fetch it for you — choose “Set up ffmpeg” on the previous "
        "screen."
    ),
    "error.no_results": "Nothing was found for that search.",
    "error.no_results.hint": (
        "Try fewer words, another spelling, or paste the link instead."
    ),
    "error.not_a_video": "No video could be found at that link.",
    "error.not_a_video.hint": (
        "Check that the link opens a video (not a channel, a search page, a "
        "photo, or a post without video)."
    ),
    "error.site_changed": "The site sent back something Viddl did not expect.",
    "error.site_changed.hint": (
        "Sites change their pages often. Waiting a day and trying again usually "
        "fixes it."
    ),
    "error.not_found": "That page could not be found (404).",
    "error.not_found.hint": (
        "The link may be mistyped or the page may have been removed."
    ),
    "error.refused": "The site refused the request (403).",
    "error.refused.hint": (
        "This is often a moment's throttling rather than a real block, so it is "
        "worth another try. If it keeps happening, the video may be restricted."
    ),
    "error.site_error": "The site had a problem of its own.",
    "error.site_error.hint": (
        "That is usually brief, so it is worth trying again in a moment."
    ),
    "error.rate_limited": "The site is rate-limiting right now.",
    "error.rate_limited.hint": "Waiting a moment and trying again usually clears it.",
    "error.offline": "Could not reach the internet.",
    "error.offline.hint": (
        "Check your Wi-Fi or ethernet connection, then try again."
    ),
    "error.connection": "The connection to the site failed.",
    "error.connection.hint": (
        "This is usually a flaky network or a firewall. Trying again often works."
    ),
    "error.disk_full": "The disk is full.",
    "error.disk_full.hint": (
        "Free up some space in your Downloads folder and try again."
    ),
    "error.not_allowed": "Viddl is not allowed to write that file.",
    "error.not_allowed.hint": "Check the permissions on your Downloads folder.",
    "error.write_failed": "There was a problem writing the file to disk.",
    "error.quality_gone": "That quality is no longer offered for this video.",
    "error.quality_gone.hint": (
        "Go back and pick another quality from the list — the options change per "
        "video."
    ),
    "error.bad_link": "That does not look like a web link.",
    "error.bad_link.hint": (
        "Copy the address from your browser's address bar and paste it again."
    ),
    "error.stopped": "The download was stopped.",
    # -- the fallbacks, for output no rule recognised -------------------------
    "error.stopped_early": "The download stopped unexpectedly.",
    "error.stopped_early.hint": (
        "Running it again usually helps. If it keeps failing, try a different "
        "quality."
    ),
    "error.generic": "Something went wrong.",
    "error.reported": "The downloader reported an error.",
    "error.reported.advice": (
        "This is usually a temporary change at the site's end. Trying again in a "
        "little while, or using another link, often works."
    ),
    "error.unfinished": "The download did not finish.",
    # -- the advice the app adds to a failure of its own accord (F3) ----------
    # It is part of the answer, so it is translated with it: a Spanish failure
    # followed by an English recommendation would be worse than either.
    "engine.stale_advice": (
        "The video engine is {days} days old, which is the usual reason a link "
        "stops working. Run `viddl --update` to replace it."
    ),
}
