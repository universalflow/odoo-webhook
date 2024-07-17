# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

from . import controllers
from . import models
from . import utils
from . import wizard

import odoo
from odoo import api, SUPERUSER_ID


def uninstall_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    act_window = env.ref('base_automation.base_automation_act', False)
    if act_window and act_window.domain and 'is_automated_action' in act_window.domain:
        act_window.domain = None

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
