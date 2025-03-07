#!/usr/bin/env python


__license__ = 'GPL v3'
__copyright__ = '2012, Kovid Goyal <kovid@kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import codecs
import os
import re
from collections import namedtuple

from calibre.customize import CatalogPlugin
from calibre.customize.conversion import DummyReporter
from calibre.library.catalogs import FIELDS
from calibre.utils.localization import _


class CSV_XML(CatalogPlugin):
    'CSV/XML catalog generator'

    Option = namedtuple('Option', 'option, default, dest, action, help')

    name = 'Catalog_CSV_XML'
    description = _('CSV/XML catalog generator')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Greg Riker'
    version = (1, 0, 0)
    file_types = {'csv', 'xml'}

    cli_options = [
            Option('--fields',
                default='all',
                dest='fields',
                action=None,
                help=_('The fields to output when cataloging books in the '
                    'database.  Should be a comma-separated list of fields.\n'
                    'Available fields: %(fields)s,\n'
                    'plus user-created custom fields.\n'
                    'Example: %(opt)s=title,authors,tags\n'
                    "Default: '%%default'\n"
                    "Applies to: CSV, XML output formats") % dict(
                        fields=', '.join(FIELDS), opt='--fields')),

            Option('--sort-by',
                default='id',
                dest='sort_by',
                action=None,
                help=_('Output field to sort on.\n'
                'Available fields: author_sort, id, rating, size, timestamp, title_sort\n'
                "Default: '%default'\n"
                "Applies to: CSV, XML output formats"))]

    def run(self, path_to_output, opts, db, notification=DummyReporter()):
        from lxml import etree

        from calibre.ebooks.metadata import authors_to_string
        from calibre.library import current_library_name
        from calibre.utils.date import isoformat
        from calibre.utils.html2text import html2text
        from calibre.utils.logging import default_log as log

        self.fmt = path_to_output.rpartition('.')[2]
        self.notification = notification
        current_library = current_library_name()
        if getattr(opts, 'library_path', None):
            current_library = os.path.basename(opts.library_path)

        if opts.verbose:
            opts_dict = vars(opts)
            log(f"{self.name}('{current_library}'): Generating {self.fmt.upper()}")
            if opts.connected_device['is_device_connected']:
                log(' connected_device: {}'.format(opts.connected_device['name']))
            if opts_dict['search_text']:
                log(" --search='{}'".format(opts_dict['search_text']))

            if opts_dict['ids']:
                log(' Book count: {}'.format(len(opts_dict['ids'])))
                if opts_dict['search_text']:
                    log(' (--search ignored when a subset of the database is specified)')

            if opts_dict['fields']:
                if opts_dict['fields'] == 'all':
                    log(' Fields: {}'.format(', '.join(FIELDS[1:])))
                else:
                    log(' Fields: {}'.format(opts_dict['fields']))

        # If a list of ids are provided, don't use search_text
        if opts.ids:
            opts.search_text = None

        data = self.search_sort_db(db, opts)

        if not len(data):
            log.error(f"\nNo matching database entries for search criteria '{opts.search_text}'")
            # raise SystemExit(1)

        # Get the requested output fields as a list
        fields = self.get_output_fields(db, opts)

        # If connected device, add 'On Device' values to data
        if opts.connected_device['is_device_connected'] and 'ondevice' in fields:
            for entry in data:
                entry['ondevice'] = db.catalog_plugin_on_device_temp_mapping[entry['id']]['ondevice']

        fm = {x: db.field_metadata.get(x, {}) for x in fields}

        if self.fmt == 'csv':
            outfile = codecs.open(path_to_output, 'w', 'utf8')

            # Write a UTF-8 BOM
            outfile.write('\ufeff')

            # Output the field headers
            outfile.write('{}\n'.format(','.join(fields)))

            # Output the entry fields
            for entry in data:
                outstr = []
                for field in fields:
                    if field.startswith('#'):
                        item = db.get_field(entry['id'], field, index_is_id=True)
                        if isinstance(item, (list, tuple)):
                            if fm.get(field, {}).get('display', {}).get('is_names', False):
                                item = ' & '.join(item)
                            else:
                                item = ', '.join(item)
                    elif field == 'library_name':
                        item = current_library
                    elif field == 'title_sort':
                        item = entry['sort']
                    else:
                        item = entry[field]

                    if item is None:
                        outstr.append('""')
                        continue
                    elif field == 'formats':
                        fmt_list = []
                        for format in item:
                            fmt_list.append(format.rpartition('.')[2].lower())
                        item = ', '.join(fmt_list)
                    elif field == 'authors':
                        item = authors_to_string(item)
                    elif field == 'tags':
                        item = ', '.join(item)
                    elif field == 'isbn':
                        # Could be 9, 10 or 13 digits, with hyphens, possibly ending in 'X'
                        item = '{}'.format(re.sub(r'[^\dX-]', '', item))
                    elif fm.get(field, {}).get('datatype') == 'datetime':
                        item = isoformat(item, as_utc=False)
                    elif field == 'comments':
                        item = item.replace('\r\n', ' ')
                        item = item.replace('\n', ' ')
                    elif fm.get(field, {}).get('datatype', None) == 'rating' and item:
                        item = f'{item/2:.2g}'

                    # Convert HTML to markdown text
                    if isinstance(item, str):
                        opening_tag = re.search(r'<(\w+)( |>)', item)
                        if opening_tag:
                            closing_tag = re.search(rf'</{opening_tag.group(1)}>$', item)
                            if closing_tag:
                                item = html2text(item)

                    outstr.append('"{}"'.format(str(item).replace('"', '""')))

                outfile.write(','.join(outstr) + '\n')
            outfile.close()

        elif self.fmt == 'xml':
            from lxml.builder import E

            if getattr(opts, 'catalog_title', None):
                root = E.calibredb(title=opts.catalog_title)
            else:
                root = E.calibredb()
            for r in data:
                try:
                    record = E.record()
                    root.append(record)

                    for field in fields:
                        if field.startswith('#'):
                            val = db.get_field(r['id'], field, index_is_id=True)
                            if not isinstance(val, str):
                                val = str(val)
                            item = getattr(E, field.replace('#', '_'))(val)
                            record.append(item)

                    for field in ('id', 'uuid', 'publisher', 'rating', 'size',
                                'isbn', 'ondevice', 'identifiers'):
                        if field in fields:
                            val = r[field]
                            if not val:
                                continue
                            if not isinstance(val, (bytes, str)):
                                if (fm.get(field, {}).get('datatype', None) ==
                                        'rating' and val):
                                    val = f'{val/2:.2g}'
                                val = str(val)
                            item = getattr(E, field)(val)
                            record.append(item)

                    if 'title' in fields:
                        title = E.title(r['title'], sort=r['sort'])
                        record.append(title)

                    if 'authors' in fields:
                        aus = E.authors(sort=r['author_sort'])
                        for au in r['authors']:
                            aus.append(E.author(au))
                        record.append(aus)

                    for field in ('timestamp', 'pubdate'):
                        if field in fields:
                            record.append(getattr(E, field)(isoformat(r[field], as_utc=False)))

                    if 'tags' in fields and r['tags']:
                        tags = E.tags()
                        for tag in r['tags']:
                            tags.append(E.tag(tag))
                        record.append(tags)

                    if 'comments' in fields and r['comments']:
                        record.append(E.comments(r['comments']))

                    if 'series' in fields and r['series']:
                        record.append(E.series(r['series'],
                            index=str(r['series_index'])))

                    if 'languages' in fields and r['languages']:
                        record.append(E.languages(r['languages']))

                    if 'cover' in fields and r['cover']:
                        record.append(E.cover(r['cover'].replace(os.sep, '/')))

                    if 'formats' in fields and r['formats']:
                        fmt = E.formats()
                        for f in r['formats']:
                            fmt.append(E.format(f.replace(os.sep, '/')))
                        record.append(fmt)

                    if 'library_name' in fields:
                        record.append(E.library_name(current_library))
                except Exception as e:
                    raise Exception('Failed to convert {} to XML with error: {}'.format(r['title'], e)) from e

            with open(path_to_output, 'wb') as f:
                f.write(etree.tostring(root, encoding='utf-8',
                    xml_declaration=True, pretty_print=True))
