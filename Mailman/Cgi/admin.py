# Copyright (C) 1998,1999,2000,2001 by the Free Software Foundation, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software 
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

"""Process and produce the list-administration options forms.

"""

import sys
import os
import re
import cgi
import sha
import urllib
import signal
from types import *
from string import lowercase, digits

from mimelib.address import unquote
# WIBNI I could just import this from mimelib.address?
from Mailman.pythonlib.rfc822 import parseaddr

from Mailman import mm_cfg
from Mailman import Utils
from Mailman import MailList
from Mailman import Errors
from Mailman import MailCommandHandler
from Mailman import i18n
from Mailman.UserDesc import UserDesc
from Mailman.htmlformat import *
from Mailman.Cgi import Auth
from Mailman.Logging.Syslog import syslog

# Set up i18n
_ = i18n._
i18n.set_language(mm_cfg.DEFAULT_SERVER_LANGUAGE)

NL = '\n'



def main():
    # Try to find out which list is being administered
    parts = Utils.GetPathPieces()
    if not parts:
        # None, so just do the admin overview and be done with it
        admin_overview()
        return
    # Get the list object
    listname = parts[0].lower()
    try:
        mlist = MailList.MailList(listname, lock=0)
    except Errors.MMListError, e:
        admin_overview(_('No such list <em>%(listname)s</em>'))
        syslog('error', 'admin.py access for non-existent list: %s',
               listname)
        return
    # Now that we know what list has been requested, all subsequent admin
    # pages are shown in that list's preferred language.
    i18n.set_language(mlist.preferred_language)
    # If the user is not authenticated, we're done.
    cgidata = cgi.FieldStorage(keep_blank_values=1)

    if not mlist.WebAuthenticate((mm_cfg.AuthListAdmin,
                                  mm_cfg.AuthSiteAdmin),
                                 cgidata.getvalue('adminpw', '')):
        if cgidata.has_key('adminpw'):
            # This is a re-authorization attempt
            msg = Bold(FontSize('+1', _('Authorization failed.'))).Format()
        else:
            msg = ''
        Auth.loginpage(mlist, 'admin', msg=msg)
        return

    # Which subcategory was requested?  Default is `general'
    if len(parts) == 1:
        category = 'general'
        category_suffix = ''
    else:
        category = parts[1]
        category_suffix = category

    # Is this a log-out request?
    if category == 'logout':
        print mlist.ZapCookie(mm_cfg.AuthListAdmin)
        Auth.loginpage(mlist, 'admin', frontpage=1)
        return

    # Sanity check
    if category not in mlist.GetConfigCategories().keys():
        category = 'general'

    # Is the request for variable details?
    varhelp = None
    if cgidata.has_key('VARHELP'):
        varhelp = cgidata['VARHELP'].value
    elif cgidata.has_key('request_login') and os.environ.get('QUERY_STRING'):
        # POST methods, even if their actions have a query string, don't get
        # put into FieldStorage's keys :-(
        qs = cgi.parse_qs(os.environ['QUERY_STRING']).get('VARHELP')
        if qs and type(qs) == ListType:
            varhelp = qs[0]
    if varhelp:
        option_help(mlist, varhelp)
        return

    # The html page document
    doc = Document()
    doc.set_language(mlist.preferred_language)

    # From this point on, the MailList object must be locked.  However, we
    # must release the lock no matter how we exit.  try/finally isn't enough,
    # because of this scenario: user hits the admin page which may take a long
    # time to render; user gets bored and hits the browser's STOP button;
    # browser shuts down socket; server tries to write to broken socket and
    # gets a SIGPIPE.  Under Apache 1.3/mod_cgi, Apache catches this SIGPIPE
    # (I presume it is buffering output from the cgi script), then turns
    # around and SIGTERMs the cgi process.  Apache waits three seconds and
    # then SIGKILLs the cgi process.  We /must/ catch the SIGTERM and do the
    # most reasonable thing we can in as short a time period as possible.  If
    # we get the SIGKILL we're screwed (because it's uncatchable and we'll
    # have no opportunity to clean up after ourselves).
    #
    # This signal handler catches the SIGTERM, unlocks the list, and then
    # exits the process.  The effect of this is that the changes made to the
    # MailList object will be aborted, which seems like the only sensible
    # semantics.
    #
    # BAW: This may not be portable to other web servers or cgi execution
    # models.
    def sigterm_handler(signum, frame, mlist=mlist):
        # Make sure the list gets unlocked...
        mlist.Unlock()
        # ...and ensure we exit, otherwise race conditions could cause us to
        # enter MailList.Save() while we're in the unlocked state, and that
        # could be bad!
        sys.exit(0)

    mlist.Lock()
    try:
        # Install the emergency shutdown signal handler
        signal.signal(signal.SIGTERM, sigterm_handler)

        if cgidata.keys():
            # There are options to change
            change_options(mlist, category, cgidata, doc)
            # Let the list sanity check the changed values
            mlist.CheckValues()
        # Additional sanity checks
        if not mlist.digestable and not mlist.nondigestable:
            add_error_message(
                doc,
                _('''You have turned off delivery of both digest and
                non-digest messages.  This is an incompatible state of
                affairs.  You must turn on either digest delivery or
                non-digest delivery or your mailing list will basically be
                unusable.'''))

        if not mlist.digestable and mlist.getDigestMemberKeys():
            add_error_message(
                doc,
                _('''You have digest members, but digests are turned
                off. Those people will not receive mail.'''))
        if not mlist.nondigestable and mlist.getRegularMemberKeys():
            add_error_message(
                doc,
                _('''You have regular list members but non-digestified mail is
                turned off.  They will receive mail until you fix this
                problem.'''))
        # Glom up the results page and print it out
        show_results(mlist, doc, category, category_suffix, cgidata)
        print doc.Format()
        mlist.Save()
    finally:
        # Now be sure to unlock the list.  It's okay if we get a signal here
        # because essentially, the signal handler will do the same thing.  And
        # unlocking is unconditional, so it's not an error if we unlock while
        # we're already unlocked.
        mlist.Unlock()



def admin_overview(msg=''):
    # Show the administrative overview page, with the list of all the lists on
    # this host.  msg is an optional error message to display at the top of
    # the page.
    #
    # This page should be displayed in the server's default language, which
    # should have already been set.
    hostname = Utils.get_domain()
    legend = _('%(hostname)s mailing lists - Admin Links')
    # The html `document'
    doc = Document()
    doc.set_language(mm_cfg.DEFAULT_SERVER_LANGUAGE)
    doc.SetTitle(legend)
    # The table that will hold everything
    table = Table(border=0, width="100%")
    table.AddRow([Center(Header(2, legend))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2,
                      bgcolor=mm_cfg.WEB_HEADER_COLOR)
    # Skip any mailing list that isn't advertised.
    advertised = []
    listnames = Utils.list_names()
    listnames.sort()

    for name in listnames:
        mlist = MailList.MailList(name, lock=0)
        if mlist.advertised:
            if mm_cfg.VIRTUAL_HOST_OVERVIEW and \
                    hostname and \
                    hostname.find(mlist.web_page_url) == -1 and \
                    mlist.web_page_url.find(hostname) == -1:
                # List is for different identity of this host - skip it.
                continue
            else:
                advertised.append(mlist)

    # Greeting depends on whether there was an error or not
    if msg:
        greeting = FontAttr(msg, color="ff5060", size="+1")
    else:
        greeting = _("Welcome!")

    welcome = []
    mailmanlink = Link(mm_cfg.MAILMAN_URL, _('Mailman')).Format()
    if not advertised:
        welcome.extend([
            greeting,
            _('''<p>There currently are no publicly-advertised %(mailmanlink)s
            mailing lists on %(hostname)s.'''),
            ])
    else:
        welcome.extend([
            greeting,
            _('''<p>Below is the collection of publicly-advertised
            %(mailmanlink)s mailing lists on %(hostname)s.  Click on a list
            name to visit the configuration pages for that list.'''),
            ])

    creatorurl = Utils.ScriptURL('create')
    mailman_owner = Utils.get_site_email()
    extra = msg and _('right ') or ''
    welcome.extend([
        _('''To visit the administrators configuration page for an
        unadvertised list, open a URL similar to this one, but with a '/' and
        the %(extra)slist name appended.  If you have the proper authority,
        you can also <a href="%(creatorurl)s">create a new mailing list</a>.

        <p>General list information can be found at '''),
        Link(Utils.ScriptURL('listinfo'),
             _('the mailing list overview page')),
        '.',
        _('<p>(Send questions and comments to '),
        Link('mailto:%s' % mailman_owner, mailman_owner),
        '.)<p>',
        ])

    table.AddRow([Container(*welcome)])
    table.AddCellInfo(max(table.GetCurrentRowIndex(), 0), 0, colspan=2)

    if advertised:
        table.AddRow(['&nbsp;', '&nbsp;'])
        table.AddRow([Bold(FontAttr(_('List'), size='+2')),
                      Bold(FontAttr(_('Description'), size='+2'))
                      ])
        highlight = 1
        for mlist in advertised:
            table.AddRow(
                [Link(mlist.GetScriptURL('admin'), Bold(mlist.real_name)),
                 mlist.description or Italic(_('[no description available]'))])
            if highlight and mm_cfg.WEB_HIGHLIGHT_COLOR:
                table.AddRowInfo(table.GetCurrentRowIndex(),
                                 bgcolor=mm_cfg.WEB_HIGHLIGHT_COLOR)
            highlight = not highlight

    doc.AddItem(table)
    doc.AddItem('<hr>')
    doc.AddItem(MailmanLogo())
    print doc.Format()



def option_help(mlist, varhelp):
    # The html page document
    doc = Document()
    doc.set_language(mlist.preferred_language)
    # Find out which category and variable help is being requested for.
    item = None
    reflist = varhelp.split('/')
    if len(reflist) == 2:
        category, varname = reflist
        options = mlist.GetConfigInfo()[category]
        for i in options:
            if i and i[0] == varname:
                item = i
                break
    # Print an error message if we couldn't find a valid one
    if not item:
        bad = _('No valid variable name found.')
        add_error_message(doc, bad)
        print doc.Format()
        return
    # Get the details about the variable
    varname, kind, params, dependancies, description, elaboration = \
             get_item_characteristics(item)
    if elaboration is None:
        elaboration = description
    #
    # Set up the document
    realname = mlist.real_name
    legend = _("""%(realname)s Mailing list Configuration Help
    <br><em>%(varname)s</em> Option""")
    
    header = Table(width='100%')
    header.AddRow([Center(Header(3, legend))])
    header.AddCellInfo(header.GetCurrentRowIndex(), 0, colspan=2,
                       bgcolor=mm_cfg.WEB_HEADER_COLOR)
    doc.SetTitle(_("Mailman %(varname)s List Option Help"))
    doc.AddItem(header)
    doc.AddItem("<b>%s</b> (%s): %s<p>" % (varname, category, description))
    doc.AddItem("%s<p>" % elaboration)

    form = Form("%s/%s" % (mlist.GetScriptURL('admin'), category))
    valtab = Table(cellspacing=3, cellpadding=4, width='100%')
    add_options_table_item(mlist, category, valtab, item, detailsp=0)
    form.AddItem(valtab)
    form.AddItem('<p>')
    form.AddItem(Center(submit_button()))
    doc.AddItem(Center(form))

    doc.AddItem(_("""<em><strong>Warning:</strong> changing this option here
    could cause other screens to be out-of-sync.  Be sure to reload any other
    pages that are displaying this option for this mailing list.  You can also
    """))

    doc.AddItem(Link('%s/%s' % (mlist.GetScriptURL('admin'), category),
                     _('return to the %(category)s options page.')))
    doc.AddItem('</em>')
    doc.AddItem(mlist.GetMailmanFooter())
    print doc.Format()



def show_results(mlist, doc, category, category_suffix, cgidata):
    # Produce the results page
    adminurl = mlist.GetScriptURL('admin')
    categories = mlist.GetConfigCategories()
    label = _(categories[category][0])

    # Set up the document's headers
    realname = mlist.real_name
    doc.SetTitle(_('%(realname)s Administration (%(label)s)'))
    doc.AddItem(Center(Header(2, _(
        '%(realname)s mailing list administration<br>%(label)s Section'))))
    doc.AddItem('<hr>')
    # This holds the two columns of links
    linktable = Table(valign='top', width='100%')
    linktable.AddRow([Center(Bold(_("Configuration Categories"))),
                      Center(Bold(_("Other Administrative Activities")))])
    linktable.AddCellInfo(linktable.GetCurrentRowIndex(), 0, colspan=2)
    # The `other links' are stuff in the right column.
    otherlinks = UnorderedList()
    otherlinks.AddItem(Link(mlist.GetScriptURL('admindb'), 
                            _('Tend to pending moderator requests')))
    otherlinks.AddItem(Link(mlist.GetScriptURL('listinfo'),
                            _('Go to the general list information page')))
    otherlinks.AddItem(Link(mlist.GetScriptURL('edithtml'),
                            _('Edit the public HTML pages')))
    otherlinks.AddItem(Link(mlist.GetBaseArchiveURL(),
                            _('Go to list archives')).Format() +
                       '<br>&nbsp;<br>')
    if mm_cfg.OWNERS_CAN_DELETE_THEIR_OWN_LISTS:
        otherlinks.AddItem(Link(mlist.GetScriptURL('rmlist'),
                                _('Delete this mailing list')).Format() +
                           _(' (requires confirmation)<br>&nbsp;<br>'))
    otherlinks.AddItem(Link('%s/logout' % adminurl,
                            # BAW: What I really want is a blank line, but
                            # adding an &nbsp; won't do it because of the
                            # bullet added to the list item.
                            '<FONT SIZE="+2"><b>%s</b></FONT>' %
                            _('Logout')))
    # These are links to other categories and live in the left column
    categorylinks_1 = categorylinks = UnorderedList()
    categorylinks_2 = ''
    categorykeys = categories.keys()
    half = len(categorykeys) / 2
    counter = 0
    for k in categorykeys:
        label = _(categories[k][0])
        url = '%s/%s' % (adminurl, k)
        if k == category:
            # Handle subcategories
            subcats = mlist.GetConfigSubCategories(k)
            if subcats:
                subcat = Utils.GetPathPieces()[-1]
                for k, v in subcats:
                    if k == subcat:
                        break
                else:
                    # The first subcategory in the list is the default
                    subcat = subcats[0][0]
                subcat_items = []
                for sub, text in subcats:
                    if sub == subcat:
                        text = Bold('[%s]' % text).Format()
                    subcat_items.append(Link(url + '/' + sub, text))
                categorylinks.AddItem(
                    Bold(label).Format() + 
                    UnorderedList(*subcat_items).Format())
            else:
                categorylinks.AddItem(Link(url, Bold('[%s]' % label)))
        else:
            categorylinks.AddItem(Link(url, label))
        counter += 1
        if counter > half:
            categorylinks_2 = categorylinks = UnorderedList()
            counter = -len(categorykeys)
    # Add all the links to the links table...
    linktable.AddRow([categorylinks_1, categorylinks_2, otherlinks])
    linktable.AddRowInfo(linktable.GetCurrentRowIndex(), valign='top')
    # ...and add the links table to the document.
    doc.AddItem(linktable)
    doc.AddItem('<hr>')
    # Now we need to craft the form that will be submitted, which will contain
    # all the variable settings, etc.  This is a bit of a kludge because we
    # know that the autoreply and members categories supports file uploads.
    if category_suffix:
        encoding = None
        if category_suffix in ('autoreply', 'members'):
            # These have file uploads
            encoding = 'multipart/form-data'
        form = Form('%s/%s' % (adminurl, category_suffix), encoding=encoding)
    else:
        form = Form(adminurl)
    # The general category supports changing the password.
    if category == 'general':
        andpassmsg = _('  (You can change your password there, too.)')
    else:
        andpassmsg = ''
    form.AddItem(
        _('''Make your changes in the following section, then submit them
        using the <em>Submit Your Changes</em> button below.''')
        + andpassmsg
        + '<p>')

    if category == 'members':
        # Figure out which subcategory we should display
        subcat = Utils.GetPathPieces()[-1]
        if subcat not in ('list', 'add', 'remove'):
            subcat = 'list'
        # Add member category specific tables
        form.AddItem(membership_options(mlist, subcat, cgidata, doc, form))
        form.AddItem(Center(submit_button()))
        form.AddItem('<hr>')
        # In "list" subcategory, we can also search for members
        if subcat == 'list':
            form.AddItem(_('''Find members by
          <a href="http://www.python.org/doc/current/lib/re-syntax.html">Python
          regular expression</a>:'''))
            form.AddItem(TextBox('findmember',
                                 value=cgidata.getvalue('findmember', ''),
                                 size='50%'))
            form.AddItem(SubmitButton('findmember_btn', _('Search...')))
            form.AddItem('<hr>')
    else:
        form.AddItem(show_variables(mlist, category, cgidata, doc))
        if category == 'general':
            form.AddItem(Center(password_inputs()))
        form.AddItem(Center(submit_button()))
        form.AddItem('<hr>')

    # And add the form
    doc.AddItem(form)
    doc.AddItem(linktable)
    doc.AddItem(mlist.GetMailmanFooter())



def show_variables(mlist, category, cgidata, doc):
    options = mlist.GetConfigInfo()[category]

    # The table containing the results
    table = Table(cellspacing=3, cellpadding=4, width='100%')

    # Get and portray the text label for the category.
    categories = mlist.GetConfigCategories()
    label = _(categories[category][0])

    table.AddRow([Center(Header(2, label))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2,
                      bgcolor=mm_cfg.WEB_HEADER_COLOR)

    # The very first item in the config info will be treated as a general
    # description if it is a string
    description = options[0]
    if isinstance(description, StringType):
        table.AddRow([description])
        table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
        options = options[1:]

    if not options:
        return table

    # Add the global column headers
    table.AddRow([Center(Bold(_('Description'))),
                  Center(Bold(_('Value')))])
    table.AddCellInfo(max(table.GetCurrentRowIndex(), 0), 0,
                      width='15%')
    table.AddCellInfo(max(table.GetCurrentRowIndex(), 0), 1,
                      width='85%')

    for item in options:
        if type(item) == StringType:
            # The very first banner option (string in an options list) is
            # treated as a general description, while any others are
            # treated as section headers - centered and italicized...
            table.AddRow([Center(Italic(item))])
            table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
        else:
            add_options_table_item(mlist, category, table, item)
    table.AddRow(['<br>'])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    return table



def add_options_table_item(mlist, category, table, item, detailsp=1):
    # Add a row to an options table with the item description and value.
    varname, kind, params, dependancies, descr, elaboration = \
             get_item_characteristics(item)
    if elaboration is None:
        elaboration = descr
    descr = get_item_gui_description(mlist, category, varname, descr, detailsp)
    val = get_item_gui_value(mlist, kind, varname, params)
    table.AddRow([descr, val])
    table.AddCellInfo(table.GetCurrentRowIndex(), 1,
                      bgcolor=mm_cfg.WEB_ADMINITEM_COLOR)
    table.AddCellInfo(table.GetCurrentRowIndex(), 0,
                      bgcolor=mm_cfg.WEB_ADMINITEM_COLOR)



def get_item_characteristics(record):
    # Break out the components of an item description from its description
    # record:
    #
    # 0 -- option-var name
    # 1 -- type
    # 2 -- entry size
    # 3 -- ?dependancies?
    # 4 -- Brief description
    # 5 -- Optional description elaboration
    if len(record) == 5:
        elaboration = None
        varname, kind, params, dependancies, descr = record
    elif len(record) == 6:
        varname, kind, params, dependancies, descr, elaboration = record
    else:
        raise ValueError, _('Badly formed options entry:\n %(record)s')
    return varname, kind, params, dependancies, descr, elaboration



def get_item_gui_value(mlist, kind, varname, params):
    """Return a representation of an item's settings."""
    if kind == mm_cfg.Radio or kind == mm_cfg.Toggle:
        # If we are returning the option for subscribe policy and this site
        # doesn't allow open subscribes, then we have to alter the value of
        # mlist.subscribe_policy as passed to RadioButtonArray in order to
        # compensate for the fact that there is one fewer option.
        # Correspondingly, we alter the value back in the change options
        # function -scott
        #
        # TBD: this is an ugly ugly hack.
        if varname[0] == '_':
            checked = 0
        else:
            checked = getattr(mlist, varname)
        if varname == 'subscribe_policy' and not mm_cfg.ALLOW_OPEN_SUBSCRIBE:
            checked = checked - 1
        return RadioButtonArray(varname, params, checked)
    elif (kind == mm_cfg.String or kind == mm_cfg.Email or
          kind == mm_cfg.Host or kind == mm_cfg.Number):
        return TextBox(varname, getattr(mlist, varname), params)
    elif kind == mm_cfg.Text:
        if params:
            r, c = params
        else:
            r, c = None, None
        val = getattr(mlist, varname)
        if not val:
            val = ''
        return TextArea(varname, val, r, c)
    elif kind == mm_cfg.EmailList:
        if params:
            r, c = params
        else:
            r, c = None, None
        res = NL.join(getattr(mlist, varname))
        return TextArea(varname, res, r, c, wrap='off')
    elif kind == mm_cfg.FileUpload:
        # like a text area, but also with uploading
        if params:
            r, c = params
        else:
            r, c = None, None
        val = getattr(mlist, varname)
        if not val:
            val = ''
        container = Container()
        container.AddItem(_('<em>Enter the text below, or...</em><br>'))
        container.AddItem(TextArea(varname, val, r, c))
        container.AddItem(_('<br><em>...specify a file to upload</em><br>'))
        container.AddItem(FileUpload(varname+'_upload', r, c))
        return container
    elif kind == mm_cfg.Select:
        if params:
           values, legend, selected = params
        else:
           values = mlist.GetAvailableLanguages()
           legend = map(_, map(Utils.GetLanguageDescr, values))
           selected = values.index(mlist.preferred_language)
        return SelectOptions(varname, values, legend, selected)
    elif kind == mm_cfg.Topics:
        # A complex and specialized widget type that allows for setting of a
        # topic name, a mark button, a regexp text box, an "add after mark",
        # and a delete button.  Yeesh!  params are ignored.
        table = Table(border=0)
        # This adds the html for the entry widget
        def makebox(i, name, pattern, desc, empty=0, table=table):
            deltag   = 'topic_delete_%02d' % i
            boxtag   = 'topic_box_%02d' % i
            reboxtag = 'topic_rebox_%02d' % i
            desctag  = 'topic_desc_%02d' % i
            wheretag = 'topic_where_%02d' % i
            addtag   = 'topic_add_%02d' % i
            newtag   = 'topic_new_%02d' % i
            if empty:
                table.AddRow([Center(Bold(_('Topic %(i)d'))),
                              Hidden(newtag)])
            else:
                table.AddRow([Center(Bold(_('Topic %(i)d'))),
                              SubmitButton(deltag, _('Delete'))])
            table.AddRow([Label(_('Topic name:')),
                          TextBox(boxtag, value=name, size=30)])
            table.AddRow([Label(_('Regexp:')),
                          TextArea(reboxtag, text=pattern,
                                   rows=4, cols=30, wrap='off')])
            table.AddRow([Label(_('Description:')),
                          TextArea(desctag, text=desc,
                                   rows=4, cols=30, wrap='soft')])
            if not empty:
                table.AddRow([SubmitButton(addtag, _('Add new item...')),
                              SelectOptions(wheretag, ('before', 'after'),
                                            (_('...before this one.'),
                                             _('...after this one.')),
                                            selected=1),
                              ])
            table.AddRow(['<hr>'])
            table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
        # Now for each element in the existing data, create a widget
        i = 1
        data = getattr(mlist, varname)
        for name, pattern, desc, empty in data:
            makebox(i, name, pattern, desc, empty)
            i += 1
        # Add one more non-deleteable widget as the first blank entry, but
        # only if there are no real entries.
        if i == 1:
            makebox(i, '', '', '', empty=1)
        return table
    elif kind == mm_cfg.Checkbox:
        return CheckBoxArray(varname, *params)



def get_item_gui_description(mlist, category, varname, descr, detailsp):
    # Return the item's description, with link to details.
    #
    # Details are not included if this is a VARHELP page, because that /is/
    # the details page!
    if detailsp:
        text = Label(descr + ' ',
                     Link(mlist.GetScriptURL('admin') +
                          '/?VARHELP=' + category + '/' + varname,
                          _('(Details)')))

    else:
        text = Label(descr)
    if varname[0] == '_':
        text = text.Format() + Label(_('''<br><em><strong>Note:</strong>
        setting this value performs an immediate action but does not modify
        permanent state.</em>''')).Format()
    return text



def membership_options(mlist, subcat, cgidata, doc, form):
    # Show the main stuff
    container = Container()
    header = Table(width="100%")
    # If we're in the list subcategory, show the membership list
    if subcat == 'add':
        header.AddRow([Center(Header(2, _('Mass Subscriptions')))])
        header.AddCellInfo(header.GetCurrentRowIndex(), 0, colspan=2,
                           bgcolor=mm_cfg.WEB_HEADER_COLOR)
        container.AddItem(header)
        mass_subscribe(mlist, container)
        return container
    if subcat == 'remove':
        header.AddRow([Center(Header(2, _('Mass Removals')))])
        header.AddCellInfo(header.GetCurrentRowIndex(), 0, colspan=2,
                           bgcolor=mm_cfg.WEB_HEADER_COLOR)
        container.AddItem(header)
        mass_remove(mlist, container)
        return container
    # Otherwise...
    header.AddRow([Center(Header(2, _('Membership List')))])
    header.AddCellInfo(header.GetCurrentRowIndex(), 0, colspan=2,
                       bgcolor=mm_cfg.WEB_HEADER_COLOR)
    container.AddItem(header)
    usertable = Table(width="90%", border='2')
    # If there are more members than allowed by chunksize, then we split the
    # membership up alphabetically.  Otherwise just display them all.
    chunksz = mlist.admin_member_chunksize
    all = mlist.getMembers()
    all.sort(lambda x, y: cmp(x.lower(), y.lower()))
    # See if the query has a regular expression
    regexp = cgidata.getvalue('findmember')
    if regexp:
        try:
            cre = re.compile(regexp, re.IGNORECASE)
        except re.error:
            add_error_message(doc, 'Bad regular expression: ' + regexp)
        else:
            all = [s for s in all if cre.search(s)]
    chunkindex = None
    bucket = None
    actionurl = None
    if len(all) < chunksz:
        members = all
    else:
        # Split them up alphabetically, and then split the alphabetical
        # listing by chunks
        buckets = {}
        for addr in all:
            members = buckets.setdefault(addr[0].lower(), [])
            members.append(addr)
        # Now figure out which bucket we want
        bucket = 'a'
        # POST methods, even if their actions have a query string, don't get
        # put into FieldStorage's keys :-(
        qs = cgi.parse_qs(os.environ['QUERY_STRING'])
        if qs.has_key('letter'):
            bucket = qs['letter'][0].lower()
            if bucket not in digits + lowercase:
                bucket = None
        if not bucket or not buckets.has_key(bucket):
            keys = buckets.keys()
            keys.sort()
            bucket = keys[0]
        members = buckets[bucket]
        action = mlist.GetScriptURL('admin') + '/members?letter=%s' % bucket
        if len(members) <= chunksz:
            form.set_action(action)
        else:
            i, r = divmod(len(members), chunksz)
            numchunks = i + (not not r * 1)
            # Now chunk them up
            chunkindex = 0
            if qs.has_key('chunk'):
                try:
                    chunkindex = int(qs['chunk'][0])
                except ValueError:
                    chunkindex = 0
                if chunkindex < 0 or chunkindex > numchunks:
                    chunkindex = 0
            members = members[chunkindex*chunksz:(chunkindex+1)*chunksz]
            # And set the action URL
            form.set_action(action + '&chunk=%s' % chunkindex)
    # So now members holds all the addresses we're going to display
    allcnt = len(all)
    if bucket:
        membercnt = len(members)
        usertable.AddRow([Center(Italic(_(
            '%(allcnt)s members total, %(membercnt)s shown')))])
    else:
        usertable.AddRow([Center(Italic(_('%(allcnt)s members total')))])
    usertable.AddCellInfo(usertable.GetCurrentRowIndex(),
                          usertable.GetCurrentCellIndex(),
                          colspan=9,
                          bgcolor=mm_cfg.WEB_ADMINITEM_COLOR)
    # Add the alphabetical links
    if bucket:
        cells = []
        for letter in digits + lowercase:
            if not buckets.get(letter):
                continue
            url = mlist.GetScriptURL('admin') + '/members?letter=%s' % letter
            if letter == bucket:
                show = Bold('[%s]' % letter.upper()).Format()
            else:
                show = letter.upper()
            cells.append(Link(url, show).Format())
        joiner = '&nbsp;'*2 + '\n'
        usertable.AddRow([Center(joiner.join(cells))])
    usertable.AddCellInfo(usertable.GetCurrentRowIndex(),
                          usertable.GetCurrentCellIndex(),
                          colspan=9,
                          bgcolor=mm_cfg.WEB_ADMINITEM_COLOR)
    usertable.AddRow([Center(h) for h in (_('unsub'),
                                          _('member address<br>member name'),
                                          _('hide'), _('nomail'),
                                          _('ack'), _('not metoo'),
                                          _('digest'), _('plain'),
                                          _('language'))])
    rowindex = usertable.GetCurrentRowIndex()
    for i in range(9):
        usertable.AddCellInfo(rowindex, i, bgcolor=mm_cfg.WEB_ADMINITEM_COLOR)
    # Find the longest name in the list
    longest = 0
    if members:
        names = filter(None, [mlist.getMemberName(s) for s in members])
        # Make the name field at least as long as the longest email address
        longest = max([len(s) for s in names + members])
    # Now populate the rows
    for addr in members:
        link = Link(mlist.GetOptionsURL(addr, obscure=1),
                    mlist.getMemberCPAddress(addr))
        fullname = mlist.getMemberName(addr)
        if fullname is None:
            fullname = ''
        name = TextBox(addr + '_realname', fullname, size=longest).Format()
        cells = [Center(CheckBox(addr + '_unsub', 'off', 0).Format()),
                 link.Format() + '<br>' +
                 name + 
                 Hidden('user', urllib.quote(addr)).Format(),
                 ]
        for opt in ('hide', 'nomail', 'ack', 'notmetoo'):
            if mlist.getMemberOption(addr,
                                     MailCommandHandler.option_info[opt]):
                value = 'on'
                checked = 1
            else:
                value = 'off'
                checked = 0
            box = CheckBox('%s_%s' % (addr, opt), value, checked)
            cells.append(Center(box).Format())
        # FIXME: use MemberAdaptor interface
        if mlist.members.has_key(addr):
            cells.append(Center(CheckBox(addr + '_digest', 'off', 0).Format()))
        else:
            cells.append(Center(CheckBox(addr + '_digest', 'on', 1).Format()))
        if mlist.getMemberOption(addr,
                                 MailCommandHandler.option_info['plain']):
            value = 'on'
            checked = 1
        else:
            value = 'off'
            checked = 0
        cells.append(Center(CheckBox('%s_plain' % addr, value, checked)))
        # User's preferred language
        langpref = mlist.getMemberLanguage(addr)
        langs = mlist.GetAvailableLanguages()
        langdescs = [_(Utils.GetLanguageDescr(lang)) for lang in langs]
        try:
            selected = langs.index(langpref)
        except ValueError:
            selected = 0
        cells.append(Center(SelectOptions(addr + '_language', langs,
                                          langdescs, selected)).Format())
        usertable.AddRow(cells)
    # Add the usertable and a legend
    container.AddItem(Center(usertable))
    legend = UnorderedList()
    legend.AddItem(
        _('<b>unsub</b> -- Click on this to unsubscribe the member.'))
    legend.AddItem(
        _("""<b>hide</b> -- Is the member's address concealed on
        the list of subscribers?"""))
    legend.AddItem(_('<b>nomail</b> -- Is delivery to the member disabled?'))
    legend.AddItem(
        _('''<b>ack</b> -- Does the member get acknowledgements of their
        posts?'''))
    legend.AddItem(
        _('''<b>not metoo</b> -- Does the member avoid copies of their own
        posts?'''))
    legend.AddItem(
        _('''<b>digest</b> -- Does the member get messages in digests?
        (otherwise, individual messages)'''))
    legend.AddItem(
        _('''<b>plain</b> -- If getting digests, does the member get plain
        text digests?  (otherwise, MIME)'''))
    legend.AddItem(_("<b>language</b> -- Language preferred by the user"))
    container.AddItem(legend.Format())

    # There may be additional chunks
    if chunkindex is not None:
        buttons = []
        url = mlist.GetScriptURL('admin') + '/members?letter=%s&' % bucket
        footer = _('''<p><em>To view more members, click on the appropriate
        range listed below:</em>''')
        chunkmembers = buckets[bucket]
        last = len(chunkmembers)
        for i in range(numchunks):
            if i == chunkindex:
                continue
            start = chunkmembers[i*chunksz]
            end = chunkmembers[min((i+1)*chunksz, last)-1]
            link = Link(url + 'chunk=%d' % i, _('from %(start)s to %(end)s'))
            buttons.append(link)
        buttons = UnorderedList(*buttons)
        container.AddItem(footer + buttons.Format() + '<p>')
    return container



def mass_subscribe(mlist, container):
    # MASS SUBSCRIBE
    table = Table(width='90%')
    # Ask whether to send a welcome message and/or to notify the admin
    table.AddRow([
        # td 1
        Label(_('Send welcome message to this batch?')),
        # td 2
        RadioButton('send_welcome_msg_to_this_batch', 0,
                    not mlist.send_welcome_msg).Format()
        + _(' no ')
        + RadioButton('send_welcome_msg_to_this_batch', 1,
                      mlist.send_welcome_msg).Format()
        + _(' yes ')
        ])
    table.AddRow([
        # td 1
        Label(_('Send notifications to the list owner? ')),
        # td 2
        RadioButton('send_notifications_to_list_owner', 0,
                    not mlist.admin_notify_mchanges).Format()
        + _(' no ')
        + RadioButton('send_notifications_to_list_owner', 1,
                      mlist.admin_notify_mchanges).Format()
        + _(' yes ')
        ])
    table.AddRow([Italic(_('Enter one address per line below...'))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    table.AddRow([Center(TextArea(name='subscribees',
                                  rows=10, cols='70%', wrap=None))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    table.AddRow([Italic(Label(_('...or specify a file to upload:'))),
                  FileUpload('subscribees_upload', cols='50')])
    container.AddItem(Center(table))



def mass_remove(mlist, container):
    # MASS UNSUBSCRIBE
    table = Table(width='90%')
    table.AddRow([
        # td 1
        Label(_('Send unsubscription acknowledgement to the user?')),
        # td 2
        RadioButton('send_unsub_ack_to_this_batch', 0, 1).Format()
        + _(' no ')
        + RadioButton('send_unsub_ack_to_this_batch', 1, 0).Format()
        + _(' yes ')
        ])
    table.AddRow([
        # td 1
        Label(_('Send notifications to the list owner?')),
        # td 2
        RadioButton('send_unsub_notifications_to_list_owner', 0,
                    not mlist.admin_notify_mchanges).Format()
        + _(' no ')
        + RadioButton('send_unsub_notifications_to_list_owner', 1,
                      mlist.admin_notify_mchanges).Format()
        + _(' yes ')
        ])
    table.AddRow([Italic(_('Enter one address per line below...'))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    table.AddRow([Center(TextArea(name='unsubscribees',
                                  rows=10, cols='70%', wrap=None))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    table.AddRow([Italic(Label(_('...or specify a file to upload:'))),
                  FileUpload('unsubscribees_upload', cols='50')])
    container.AddItem(Center(table))



def password_inputs():
    table = Table(cellspacing=3, cellpadding=4)
    table.AddRow([Center(Header(2, _('Change list ownership passwords')))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2,
                      bgcolor=mm_cfg.WEB_HEADER_COLOR)
    table.AddRow([_("""\
<a name="passwords">The</a> <em>list administrators</em> are the people who
have ultimate control over all parameters of this mailing list.  They are able
to change any list configuration variable available through these
administration web pages.

<p>The <em>list moderators</em> have more limited permissions; they are not
able to change any list configuration variable, but they are allowed to tend
to pending administration requests, including approving or rejecting held
subscription requests, and disposing of held postings.  Of course, the
<em>list administrators</em> can also tend to pending requests.

<p>In order to split the list ownership duties into administrators and
moderators, you must set a separate moderator password in the fields below,
and also provide the email addresses of the list moderators in the section
above.""")])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, colspan=2)
    # Set up the admin password table on the left
    atable = Table(border=0, cellspacing=3, cellpadding=4,
                   bgcolor=mm_cfg.WEB_ADMINPW_COLOR)
    atable.AddRow([Label(_('Enter new administrator password:')),
                   PasswordBox('newpw', size=20)])
    atable.AddRow([Label(_('Confirm administator password:')),
                   PasswordBox('confirmpw', size=20)])
    # Set up the moderator password table on the right
    mtable = Table(border=0, cellspacing=3, cellpadding=4,
                   bgcolor=mm_cfg.WEB_ADMINPW_COLOR)
    mtable.AddRow([Label(_('Enter new moderator password:')),
                   PasswordBox('newmodpw', size=20)])
    mtable.AddRow([Label(_('Confirm moderator password:')),
                   PasswordBox('confirmmodpw', size=20)])
    # Add these tables to the overall password table
    table.AddRow([atable, mtable])
    return table



def submit_button():
    table = Table(border=0, cellspacing=0, cellpadding=2)
    table.AddRow([Bold(SubmitButton('submit', _('Submit Your Changes')))])
    table.AddCellInfo(table.GetCurrentRowIndex(), 0, align='middle')
    return table



# Options processing
def get_valid_value(mlist, prop, wtype, val, dependant):
    if wtype == mm_cfg.Radio or wtype == mm_cfg.Toggle:
        if type(val) <> IntType:
            try:
                val = int(val)
            except ValueError:
                pass
                # Don't know what to do here...
            return val
    elif wtype == mm_cfg.String or wtype == mm_cfg.Text:
        return val
    elif wtype == mm_cfg.Email:
        # BAW: We must allow blank values otherwise reply_to_address can't be
        # cleared.  This is currently the only mm_cfg.Email type widget in the
        # interface, so watch out if we ever add any new ones.
        if val:
            Utils.ValidateEmail(val)
        return val
    elif wtype == mm_cfg.EmailList:
        def validp(addr):
            try:
                Utils.ValidateEmail(addr)
                return 1
            except Errors.EmailAddressError:
                return 0
        val = [addr for addr in [s.strip() for s in val.split(NL)]
               if validp(addr)]
        return val
    elif wtype == mm_cfg.Host:
        return val
    elif wtype == mm_cfg.Number:
        num = -1
        try:
            num = int(val)
        except ValueError:
            # TBD: a float???
            try:
                num = float(val)
            except ValueError:
                pass
        if num < 0:
            return getattr(mlist, prop)
        return num
    elif wtype == mm_cfg.Select:
        return val
    elif wtype == mm_cfg.Checkbox:
        if type(val) is not ListType:
            return [val]
        return val
    else:
        # Should never get here...
        return val



def change_options(mlist, category, cgidata, doc):
    confirmed = 0
    # Handle changes to the list moderator password.  Do this before checking
    # the new admin password, since the latter will force a reauthentication.
    new = cgidata.getvalue('newmodpw', '').strip()
    confirm = cgidata.getvalue('confirmmodpw', '').strip()
    if new or confirm:
        if new == confirm:
            mlist.mod_password = sha.new(new).hexdigest()
            # No re-authentication necessary because the moderator's
            # password doesn't get you into these pages.
        else:
            add_error_message(
                doc, _('Moderator passwords did not match'),
                tag=_('Error: '))
    # Handle changes to the list administator password
    new = cgidata.getvalue('newpw', '').strip()
    confirm = cgidata.getvalue('confirmpw', '').strip()
    if new or confirm:
        if new == confirm:
            mlist.password = sha.new(new).hexdigest()
            # Set new cookie
            print mlist.MakeCookie(mm_cfg.AuthListAdmin)
        else:
            add_error_message(
                doc, _('Administator passwords did not match'),
                tag=_('Error: '))

    # Give the individual gui item a chance to process the form data
    categories = mlist.GetConfigCategories()
    label, gui = categories[category]
    if hasattr(gui, 'HandleForm'):
        gui.HandleForm(mlist, cgidata, doc)
        return

    # for some reason, the login page mangles important values for the list
    # such as .real_name so we only process these changes if the category
    # is not "members" and the request is not from the login page
    # -scott 19980515
    #
    if category != 'members' and \
            not cgidata.has_key("request_login") and \
            len(cgidata.keys()) > 1:
        # then
        if cgidata.has_key("subscribe_policy"):
            if not mm_cfg.ALLOW_OPEN_SUBSCRIBE:
                #
                # we have to add one to the value because the
                # page didn't present an open list as an option
                #
                page_setting = int(cgidata["subscribe_policy"].value)
                cgidata["subscribe_policy"].value = str(page_setting + 1)
        opt_list = mlist.GetConfigInfo()[category]
        for item in opt_list:
            if type(item) <> TupleType or len(item) < 5:
                continue
            property, kind, args, deps, desc = item[0:5]
            if cgidata.has_key(property+'_upload') and \
                   cgidata[property+'_upload'].value:
                val = cgidata[property+'_upload'].value
            elif not cgidata.has_key(property):
                continue
            elif type(cgidata[property]) == ListType:
                val = [x.value for x in cgidata[property]]
            else:
                val = cgidata[property].value
            try:
                value = get_valid_value(mlist, property, kind, val, deps)
            except Errors.EmailAddressError:
                add_error_message(
                    doc,
                    _('Bad email address for option %(property)s: %(val)s'),
                    tag=_('Error: '))
                continue
            # BAW: Ugly, ugly hack for "do immediately" pseudo-options
            if property[0] == '_':
                if property == '_mass_catchup' and value:
                    mlist.usenet_watermark = None
                elif property == '_new_volume' and value:
                    mlist.bump_digest_volume()
                elif property == '_send_digest_now' and value:
                    mlist.send_digest_now()
            elif getattr(mlist, property) <> value:
                # TBD: Ensure that mlist.real_name differs only in letter
                # case.  Otherwise a security hole can potentially be opened
                # when using an external archiver.  This seems ad-hoc and
                # could use a more general security policy.
                if property == 'real_name' and \
                       value.lower() <> mlist._internal_name.lower():
                    # then don't install this value.
                    doc.AddItem(_("""<p><b>real_name</b> attribute not
                    changed!  It must differ from the list's name by case
                    only.<p>"""))
                    continue
                # Watch for changes to preferred_language.  If found, make
                # sure that the response is generated in the new language.
                if property == 'preferred_language':
                    i18n.set_language(value)
                setattr(mlist, property, value)
    # mass subscription, removal processing for members category
    subscribers = ''
    subscribers += cgidata.getvalue('subscribees', '')
    subscribers += cgidata.getvalue('subscribees_upload', '')
    if subscribers:
        entries = filter(
            None,
            [n.strip() for n in subscribers.replace('\r','').split(NL)])
        send_welcome_msg = mlist.send_welcome_msg
        if cgidata.has_key('send_welcome_msg_to_this_batch'):
            send_welcome_msg = int(
                cgidata.getvalue('send_welcome_msg_to_this_batch'))
        send_admin_notif = mlist.admin_notify_mchanges
        if cgidata.has_key('send_notifications_to_list_owner'):
            send_admin_notif = int(
                cgidata.getvalue('send_notifications_to_list_owner'))
        digest = 0
        if not mlist.digestable:
            digest = 0
        if not mlist.nondigestable:
            digest = 1
        subscribe_errors = []
        subscribe_success = []

        for entry in entries:
            fullname, address = parseaddr(entry)
            userdesc = UserDesc(address, fullname, digest=digest)
            try:
                mlist.ApprovedAddMember(userdesc, send_welcome_msg,
                                       send_admin_notif)
            except Errors.MMAlreadyAMember:
                subscribe_errors.append((entry, _('Already a member')))
            except Errors.MMBadEmailError:
                if userdesc.address == '':
                    subscribe_errors.append((_('&lt;blank line&gt;'),
                                             _('Bad/Invalid email address')))
                else:
                    subscribe_errors.append((entry,
                                             _('Bad/Invalid email address')))
            except Errors.MMHostileAddress:
                subscribe_errors.append(
                    (entry, _('Hostile address (illegal characters)')))
            else:
                subscribe_success.append(entry)
        if subscribe_success:
            doc.AddItem(Header(5, _('Successfully Subscribed:')))
            doc.AddItem(UnorderedList(*subscribe_success))
            doc.AddItem('<p>')
        if subscribe_errors:
            doc.AddItem(Header(5, _('Error Subscribing:')))
            items = ['%s -- %s' % (x0, x1) for x0, x1 in subscribe_errors]
            doc.AddItem(UnorderedList(*items))
            doc.AddItem('<p>')
    # Unsubscriptions
    removals = ''
    if cgidata.has_key('unsubscribees'):
        removals += cgidata['unsubscribees'].value
    if cgidata.has_key('unsubscribees_upload') and \
           cgidata['unsubscribees_upload'].value:
        removals += cgidata['unsubscribees_upload'].value
    if removals:
        removals.replace('\r', '')
        names = filter(None, [unquote(n.strip()) for n in removals.split(NL)])
        send_unsub_notifications = int(
            cgidata['send_unsub_notifications_to_list_owner'].value)
        userack = int(
            cgidata['send_unsub_ack_to_this_batch'].value)
        unsubscribe_errors = []
        unsubscribe_success = []
        for addr in names:
            try:
                mlist.ApprovedDeleteMember(
                    addr, whence='admin mass unsub',
                    admin_notif=send_unsub_notifications,
                    userack=userack)
                unsubscribe_success.append(addr)
            except Errors.MMNoSuchUserError:
                unsubscribe_errors.append(addr)
        if unsubscribe_success:
            doc.AddItem(Header(5, _('Successfully Unsubscribed:')))
            doc.AddItem(UnorderedList(*unsubscribe_success))
            doc.AddItem('<p>')
        if unsubscribe_errors:
            doc.AddItem(Header(3, Bold(FontAttr(
                _('Cannot unsubscribe non-members:'),
                color='#ff0000', size='+2')).Format()))
            doc.AddItem(UnorderedList(*unsubscribe_errors))
            doc.AddItem('<p>')
    #
    # do the user options for members category
    if cgidata.has_key('user'):
        user = cgidata["user"]
        if type(user) is ListType:
            users = []
            for ui in range(len(user)):
                users.append(urllib.unquote(user[ui].value))
        else:
            users = [urllib.unquote(user.value)]
        errors = []
        for user in users:
            if cgidata.has_key('%s_unsub' % user):
                try:
                    mlist.ApprovedDeleteMember(user)
                except Errors.MMNoSuchUserError:
                    errors.append((user, _('Not subscribed')))
                continue
            value = cgidata.has_key('%s_digest' % user)
            try:
                mlist.setMemberOption(user, mm_cfg.Digests, value)
            except (Errors.NotAMemberError,
                    Errors.AlreadyReceivingDigests,
                    Errors.AlreadyReceivingRegularDeliveries,
                    Errors.CantDigestError,
                    Errors.MustDigestError):
                pass

            newname = cgidata.getvalue(user+'_realname', '')
            mlist.setMemberName(user, newname)

            newlang = cgidata.getvalue(user+'_language')
            oldlang = mlist.getMemberLanguage(user)
            if newlang and newlang <> oldlang:
                mlist.setMemberLanguage(user, newlang)
                  
            for opt in ("hide", "nomail", "ack", "notmetoo", "plain"):
                opt_code = MailCommandHandler.option_info[opt]
                if cgidata.has_key('%s_%s' % (user, opt)):
                    mlist.setMemberOption(user, opt_code, 1)
                else:
                    mlist.setMemberOption(user, opt_code, 0)
        if errors:
            doc.AddItem(Header(5, _("Error Unsubscribing:")))
            items = ['%s -- %s' % (x[0], x[1]) for x in errors]
            doc.AddItem(apply(UnorderedList, tuple((items))))
            doc.AddItem("<p>")



def add_error_message(doc, errmsg, tag=None, *args):
    if tag is None:
        tag = _('Warning: ')
    doc.AddItem(Header(3, Bold(FontAttr(
        _(tag), color=mm_cfg.WEB_ERROR_COLOR, size="+2")).Format() +
                       Italic(errmsg % args).Format()))
