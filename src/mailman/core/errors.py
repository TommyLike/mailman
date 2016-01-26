# Copyright (C) 1998-2016 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# GNU Mailman is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# GNU Mailman.  If not, see <http://www.gnu.org/licenses/>.

"""Legacy Mailman exceptions.

This module is largely obsolete, though not all exceptions in use have been
migrated to their proper location.  There are still a number of Mailman 2.1
exceptions floating about in here too.

The right place for exceptions is in the interface module for their related
interfaces.
"""


__all__ = [
    'DiscardMessage',
    'HandlerError',
    'HoldMessage',
    'LostHeldMessage',
    'RESTError',
    'ReadOnlyPATCHRequestError',
    'RejectMessage',
    'UnknownPATCHRequestError',
    ]



class MailmanError(Exception):
    """Base class for all Mailman errors."""
    pass



# Exceptions for admin request database
class LostHeldMessage(MailmanError):
    """Held message was lost."""
    pass



def _(s):
    return s


# Exceptions for the Handler subsystem
class HandlerError(MailmanError):
    """Base class for all handler errors."""

    def __init__(self, message=None):
        self.message = message

    def __str__(self):
        return self.message


class HoldMessage(HandlerError):
    """Base class for all message-being-held short circuits."""

    # funky spelling is necessary to break import loops
    reason = _('For some unknown reason')

    def reason_notice(self):
        return self.reason

    # funky spelling is necessary to break import loops
    rejection = _('Your message was rejected')

    def rejection_notice(self, mlist):
        return self.rejection


class DiscardMessage(HandlerError):
    """The message can be discarded with no further action"""


class RejectMessage(HandlerError):
    """The message will be bounced back to the sender"""



class RESTError(MailmanError):
    """Base class for REST API errors."""


class UnknownPATCHRequestError(RESTError):
    """A PATCH request contained an unknown attribute."""

    def __init__(self, attribute):
        self.attribute = attribute


class ReadOnlyPATCHRequestError(RESTError):
    """A PATCH request contained a read-only attribute."""

    def __init__(self, attribute):
        self.attribute = attribute
