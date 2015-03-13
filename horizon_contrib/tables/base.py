# -*- coding: UTF-8 -*-
from operator import attrgetter

import six
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.forms.models import fields_for_model
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _
from horizon import tables
from horizon.tables import Column
from horizon.tables.base import DataTableMetaclass, DataTableOptions
from horizon_contrib.common.content_type import get_class

from .filters import filter_m2m


class ModelTableOptions(DataTableOptions):

    """provide new params for Table Meta class

        .. attribute:: model_class

        String or Django Model.

        .. attribute:: order_by

        Array for ordering default is ('id')
    """

    def __init__(self, options):

        self.model_class = getattr(options, 'model_class', None)
        self.order_by = getattr(options, 'order_by', ("-id"))
        super(ModelTableOptions, self).__init__(options)


class ModelTableMetaclass(DataTableMetaclass):

    def __new__(mcs, name, bases, attrs):
        # Process options from Meta
        class_name = name
        attrs["_meta"] = opts = ModelTableOptions(attrs.get("Meta", None))
        # Gather columns; this prevents the column from being an attribute
        # on the DataTable class and avoids naming conflicts.
        columns = []
        for attr_name, obj in attrs.items():
            if issubclass(type(obj), (opts.column_class, Column)):
                column_instance = attrs.pop(attr_name)
                column_instance.name = attr_name
                column_instance.classes.append('normal_column')
                columns.append((attr_name, column_instance))
        columns.sort(key=lambda x: x[1].creation_counter)

        # Iterate in reverse to preserve final order
        for base in bases[::-1]:
            if hasattr(base, 'base_columns'):
                columns = base.base_columns.items() + columns
        attrs['base_columns'] = SortedDict(columns)

        # If the table is in a ResourceBrowser, the column number must meet
        # these limits because of the width of the browser.
        if opts.browser_table == "navigation" and len(columns) > 3:
            raise ValueError("You can only assign three column to %s."
                             % class_name)
        if opts.browser_table == "content" and len(columns) > 2:
            raise ValueError("You can only assign two columns to %s."
                             % class_name)

        if opts.columns:
            # Remove any columns that weren't declared if we're being explicit
            # NOTE: we're iterating a COPY of the list here!
            for column_data in columns[:]:
                if column_data[0] not in opts.columns:
                    columns.pop(columns.index(column_data))
            # Re-order based on declared columns
            columns.sort(key=lambda x: attrs['_meta'].columns.index(x[0]))
        # Add in our auto-generated columns
        if opts.multi_select and opts.browser_table != "navigation":
            multi_select = opts.column_class("multi_select",
                                             verbose_name="",
                                             auto="multi_select")
            multi_select.classes.append('multi_select_column')
            columns.insert(0, ("multi_select", multi_select))
        if opts.actions_column:
            actions_column = opts.column_class("actions",
                                               verbose_name=_("Actions"),
                                               auto="actions")
            actions_column.classes.append('actions_column')
            columns.append(("actions", actions_column))
        # Store this set of columns internally so we can copy them per-instance
        attrs['_columns'] = SortedDict(columns)

        # Gather and register actions for later access since we only want
        # to instantiate them once.
        # (list() call gives deterministic sort order, which sets don't have.)
        actions = list(set(opts.row_actions) | set(opts.table_actions))
        actions.sort(key=attrgetter('name'))
        actions_dict = SortedDict([(action.name, action())
                                   for action in actions])
        attrs['base_actions'] = actions_dict
        if opts._filter_action:
            # Replace our filter action with the instantiated version
            opts._filter_action = actions_dict[opts._filter_action.name]

        # Create our new class!
        return type.__new__(mcs, name, bases, attrs)


class ModelTable(six.with_metaclass(ModelTableMetaclass, tables.DataTable)):

    """
    Django model class or string(content_type).

    .. attribute:: model_class String or django model class

    note: best way is ModelClass because find by content_type
    makes additional db queries

    .. attribute:: order_by is default to ("-id")

    """

    def __init__(self, request, data=None, needs_form_wrapper=None, **kwargs):

        super(ModelTable, self).__init__(
            request=request,
            data=data,
            needs_form_wrapper=needs_form_wrapper,
            **kwargs)

        # get fields and makes columns
        fields = fields_for_model(
            self._model_class, fields=getattr(self._meta, "columns", []))

        columns = {}

        many = [i.name for i in self._model_class._meta.many_to_many]

        for name, field in fields.iteritems():
            column_kwargs = {
                "verbose_name": getattr(field, "label", name),
                "form_field": field
            }
            if name in many:
                column_kwargs["filters"] = filter_m2m,
            column = tables.Column(name, **column_kwargs)
            column.table = self
            columns[name] = column

        actions = self._columns.pop("actions")
        columns["actions"] = actions
        self._columns.update(columns)
        self.columns.update(columns)
        self._populate_data_cache()

        super(ModelTable, self).__init__(
            request=request,
            data=data,
            needs_form_wrapper=needs_form_wrapper,
            **kwargs)

        has_get_table_data = hasattr(
            self, 'get_table_data') and callable(self.get_table_data)

        if not has_get_table_data and not hasattr(self, "model_class"):
            cls_name = self.__class__.__name__
            raise NotImplementedError('You must define either a model_class or\
                                      "get_table_data" '
                                      'method on %s.' % cls_name)

    @property
    def _model_class(self):
        mcs = getattr(
            self._meta, "model_class", getattr(self, "model_class", None))
        if isinstance(mcs, basestring):
            try:
                self.model_class = get_class(mcs)
            except Exception, e:
                raise e
        mcls = getattr(self, "model_class", mcs)
        if not mcls:
            raise Exception("Missing model_class or override one \
                            of get_table_data, get_paginator_data")
        return mcls

    def get_table_data(self):
        """generic implementation
        returns queryset or list dataset for paginator
        """
        object_list = []
        if self._model_class is None and not callable(self.get_table_data):
            raise Exception(
                "you must specify ``model_class`` or override get_table_data")
        object_list = self._model_class.objects.all().order_by(
            self._meta.order_by)
        return object_list


class PaginationMixin(object):

    """

    Turn off render pagination into template.

    .. attribute:: pagination

    Django model class.

    .. attribute:: model_class or string(content_type) see ModelTable

        Turn off render `Show all` into template.

    .. attribute:: show_all_url

    .. attribute:: position

        Position of pagionation Top, Bottom, Both

    """
    order_by = ("-id")

    page = "1"
    pagination = True
    position = "bottom"
    show_all_url = True

    PAGINATION_COUNT = "25"
    _paginator = None

    def get_paginator_data(self):
        """generic implementation which expect modeltable inheritence
        """
        return self.get_table_data()

    @property
    def get_page(self):
        """returns int page"""
        page = None
        try:
            page = int(self.page)  # fail only if set all
        except Exception:
            # swallow
            pass
        return page

    def get_page_data(self, page="1"):
        """returns data for specific page
        default returns for first page
        """

        if not self.paginator:
            raise RuntimeError('missing paginator instance ')

        if page:
            self.page = page
        try:
            if not self.page == "all":
                objects = self.paginator.page(self.page)
            elif self.show_all_url:
                objects = self.get_paginator_data()
        except EmptyPage:
            objects = self.paginator.page(self.paginator.num_pages)
        return objects

    @property
    def paginator(self):
        """returns instance of paginator
        """
        if not self._paginator:
            try:
                self._paginator = Paginator(
                    self.get_paginator_data(), self.PAGINATION_COUNT)
            except Exception, e:
                raise e
        return self._paginator

    def previous_page_number(self):
        if not self.get_page is None:
            return self.get_page - 1
        return None

    def next_page_number(self):
        if not self.get_page is None:
            return self.get_page + 1
        return None

    def has_previous(self):
        if not self.get_page is None:
            if self.get_page == 1:
                return False
            return True
        return False

    def has_next(self):
        if not self.get_page is None:
            if (self.get_page + 1) > self.paginator.num_pages:
                return False
            return True
        return False

    def has_more_data(self):
        """in default state is disabled, but can be used, but must be
        implemented some extra methods
        """
        return False

    def __init__(self, *args, **kwargs):
        super(PaginationMixin, self).__init__(*args, **kwargs)


class PaginatedTable(ModelTable, PaginationMixin):

    """Paginated datatable with simple implementation which uses django Paginator

    note(majklk): this table uses custom table template
    """

    def __init__(self, *args, **kwargs):

        self._meta.template = \
                            "horizon_contrib/tables/_paginated_data_table.html"

        super(PaginatedTable, self).__init__(*args, **kwargs)

        has_get_table_data = hasattr(
            self, 'get_paginator_data') and callable(self.get_paginator_data)

        if not has_get_table_data and not hasattr(self, "model_class"):
            cls_name = self.__class__.__name__
            raise NotImplementedError('You must define either a model_class \
                                       or "get_paginator_data" '
                                      'method on %s.' % cls_name)

        self.PAGINATION_COUNT = getattr(
            settings, "PAGINATION_COUNT", self.PAGINATION_COUNT)


class PaginatedModelTable(ModelTable, PaginationMixin):

    """generic paginated model table
    """

    def __init__(self, *args, **kwargs):

        self._meta.template = \
                            "horizon_contrib/tables/_paginated_data_table.html"

        super(PaginatedModelTable, self).__init__(*args, **kwargs)

        has_get_table_data = hasattr(
            self, 'get_paginator_data') and callable(self.get_paginator_data)

        if not has_get_table_data and not hasattr(self, "model_class"):
            cls_name = self.__class__.__name__
            raise NotImplementedError('You must define either a model_class \
                                       or "get_paginator_data" '
                                      'method on %s.' % cls_name)

        self.PAGINATION_COUNT = getattr(
            settings, "PAGINATION_COUNT", self.PAGINATION_COUNT)