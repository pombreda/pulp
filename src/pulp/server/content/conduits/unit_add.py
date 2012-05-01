# -*- coding: utf-8 -*-
#
# Copyright © 2011 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public
# License as published by the Free Software Foundation; either version
# 2 of the License (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied,
# including the implied warranties of MERCHANTABILITY,
# NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
# have received a copy of GPLv2 along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

"""
Contains conduit classes containing methods for adding and linking units in
Pulp, as well as associating them to a given repository.
"""

from gettext import gettext as _
import logging
import sys

import pulp.server.content.conduits._common as common_utils
from pulp.server.content.conduits._base import BaseImporterConduit, ImporterConduitException
import pulp.server.content.types.database as types_db
from pulp.server.content.plugins.model import Unit
from pulp.server.exceptions import MissingResource
import pulp.server.managers.factory as manager_factory
from pulp.server.managers.repo.unit_association import OWNER_TYPE_IMPORTER

from pulp.server.managers.repo.unit_association_query import Criteria # shadow for importing by plugins

# -- constants ---------------------------------------------------------------

_LOG = logging.getLogger(__name__)

# -- classes -----------------------------------------------------------------

class UnitAddConduit(BaseImporterConduit):
    """
    Used to communicate back into the Pulp server while an importer performs
    commands related to adding and linking units.

    Instances of this class should *not* be cached between calls into the importer.
    Each call will be issued its own conduit instance that is scoped
    to that run of the operation alone.

    Instances of this class are thread-safe. The importer implementation is
    allowed to do whatever threading makes sense to optimize its process.
    Calls into this instance do not have to be coordinated for thread safety,
    the instance will take care of it itself.
    """

    def __init__(self, repo_id, importer_id):
        """
        @param repo_id: identifies the repo being synchronized
        @type  repo_id: str

        @param importer_id: identifies the importer performing the sync
        @type  importer_id: str
        """
        BaseImporterConduit.__init__(self, repo_id, importer_id)

        self._repo_manager = manager_factory.repo_manager()
        self._importer_manager = manager_factory.repo_importer_manager()
        self._sync_manager = manager_factory.repo_sync_manager()
        self._association_manager = manager_factory.repo_unit_association_manager()
        self._association_query_manager = manager_factory.repo_unit_association_query_manager()
        self._content_manager = manager_factory.content_manager()
        self._content_query_manager = manager_factory.content_query_manager()

        self._added_count = 0
        self._updated_count = 0

    def __str__(self):
        return _('UnitAddConduit for repository [%(r)s]') % {'r' : self.repo_id}

    def get_units(self, criteria=None):
        """
        Returns the collection of content units associated with the repository
        being synchronized.

        Units returned from this call will have the id field populated and are
        useable in any calls in this conduit that require the id field.

        @param criteria: used to scope the returned results or the data within;
               the Criteria class can be imported from this module
        @type  criteria: L{Criteria}

        @return: list of unit instances
        @rtype:  list of L{AssociatedUnit}
        """

        try:
            units = self._association_query_manager.get_units_across_types(self.repo_id, criteria=criteria)

            all_units = []

            # Load all type definitions in use so we don't hammer the database
            unique_type_defs = set([u['unit_type_id'] for u in units])
            type_defs = {}
            for def_id in unique_type_defs:
                type_def = types_db.type_definition(def_id)
                type_defs[def_id] = type_def

            # Convert to transfer object
            for unit in units:
                type_id = unit['unit_type_id']
                u = common_utils.to_plugin_unit(unit, type_defs[type_id])
                all_units.append(u)

            return all_units

        except Exception, e:
            _LOG.exception('Exception from server requesting all content units for repository [%s]' % self.repo_id)
            raise ImporterConduitException(e), None, sys.exc_info()[2]

    def init_unit(self, type_id, unit_key, metadata, relative_path):
        """
        Initializes the Pulp representation of a content unit. The conduit will
        use the provided information to generate any unit metadata that it needs
        to. A populated transfer object representation of the unit will be
        returned from this call. The returned unit should be used in subsequent
        calls to this conduit.

        This call makes no changes to the Pulp server. At the end of this call,
        the unit's id field will *not* be populated.

        The unit_key and metadata will be merged as they are saved in Pulp to
        form the full representation of the unit. If values are specified in
        both dictionaries, the unit_key value takes precedence.

        If the importer wants to save the bits for the unit, the relative_path
        value should be used to indicate a unique -- with respect to the type
        of unit -- relative path where it will be saved. Pulp will convert this
        into an absolute path on disk where the unit should actually be saved.
        The absolute path is stored in the returned unit object.

        @param type_id: must correspond to a type definition in Pulp
        @type  type_id: str

        @param unit_key: dictionary of whatever fields are necessary to uniquely
                         identify this unit from others of the same type
        @type  unit_key: dict

        @param metadata: dictionary of key-value pairs to describe the unit
        @type  metadata: dict

        @param relative_path: see above; may be None
        @type  relative_path: str

        @return: object representation of the unit, populated by Pulp with both
                 provided and derived values
        @rtype:  L{Unit}
        """

        try:
            # Generate the storage location
            if relative_path is not None:
                path = self._content_query_manager.request_content_unit_file_path(type_id, relative_path)
            else:
                path = None
            u = Unit(type_id, unit_key, metadata, path)
            return u
        except Exception, e:
            _LOG.exception('Exception from server requesting unit filename for relative path [%s]' % relative_path)
            raise ImporterConduitException(e), None, sys.exc_info()[2]

    def save_unit(self, unit):
        """
        Performs two distinct steps on the Pulp server:
        - Creates or updates Pulp's knowledge of the content unit.
        - Associates the unit to the repository being synchronized.

        This call is idempotent. If the unit already exists or the association
        already exists, this call will have no effect.

        A reference to the provided unit is returned from this call. This call
        will populate the unit's id field with the UUID for the unit.

        @param unit: unit object returned from the init_unit call
        @type  unit: L{Unit}

        @return: object reference to the provided unit, its state updated from the call
        @rtype:  L{Unit}
        """
        try:
            # Save or update the unit
            pulp_unit = common_utils.to_pulp_unit(unit)
            try:
                existing_unit = self._content_query_manager.get_content_unit_by_keys_dict(unit.type_id, unit.unit_key)
                unit.id = existing_unit['_id']
                self._content_manager.update_content_unit(unit.type_id, unit.id, pulp_unit)
                self._updated_count += 1
            except MissingResource:
                unit.id = self._content_manager.add_content_unit(unit.type_id, None, pulp_unit)
                self._added_count += 1

            # Associate it with the repo
            self._association_manager.associate_unit_by_id(self.repo_id, unit.type_id, unit.id, OWNER_TYPE_IMPORTER, self.importer_id)

            return unit
        except Exception, e:
            _LOG.exception(_('Content unit association failed [%s]' % str(unit)))
            raise ImporterConduitException(e), None, sys.exc_info()[2]

    def link_unit(self, from_unit, to_unit, bidirectional=False):
        """
        Creates a reference between two content units. The semantics of what
        this relationship means depends on the types of content units being
        used; this call simply ensures that Pulp will save and make available
        the indication that a reference exists from one unit to another.

        By default, the reference will only exist on the from_unit side. If
        the bidirectional flag is set to true, a second reference will be created
        on the to_unit to refer back to the from_unit.

        Units passed to this call must have their id fields set by the Pulp server.

        @param from_unit: owner of the reference
        @type  from_unit: L{Unit}

        @param to_unit: will be referenced by the from_unit
        @type  to_unit: L{Unit}
        """
        try:
            self._content_manager.link_referenced_content_units(from_unit.type_id, from_unit.id, to_unit.type_id, [to_unit.id])

            if bidirectional:
                self._content_manager.link_referenced_content_units(to_unit.type_id, to_unit.id, from_unit.type_id, [from_unit.id])
        except Exception, e:
            _LOG.exception(_('Child link from parent [%s] to child [%s] failed' % (str(from_unit), str(to_unit))))
            raise ImporterConduitException(e), None, sys.exc_info()[2]
