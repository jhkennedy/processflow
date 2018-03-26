import os
import sys
import logging
import time
import re
import json

from pprint import pformat
from time import sleep
from datetime import datetime
from shutil import copyfile, copy2, rmtree
from bs4 import BeautifulSoup

from lib.events import EventList
from lib.slurm import Slurm
from JobStatus import JobStatus, StatusMap
from lib.util import (render,
                      print_debug,
                      print_line)


class APrimeDiags(object):
    def __init__(self, config, event_list):
        """
        Setup class attributes
        """
        self.event_list = event_list
        self.inputs = {
            'account': '',
            'resource_path': '',
            'simulation_start_year': '',
            'target_host_path': '',
            'ui': '',
            'web_dir': '',
            'host_url': '',
            'experiment': '',
            'run_scripts_path': '',
            'year_set': '',
            'input_path': '',
            'start_year': '',
            'end_year': '',
            'output_path': '',
            'template_path': '',
            'test_atm_res': '',
            'test_mpas_mesh_name': '',
            'aprime_code_path': '',
            'filemanager': ''
        }
        self.start_time = None
        self.end_time = None
        self.output_path = config['output_path']
        self.filemanager = config['filemanager']
        self.config = {}
        self.host_suffix = '/index.html'
        self.status = JobStatus.INVALID
        self._type = 'aprime_diags'
        self.year_set = config['year_set']
        self.start_year = config['start_year']
        self.end_year = config['end_year']
        self.job_id = 0
        self.depends_on = []
        self.prevalidate(config)

    def __str__(self):
        sanitized = {k: v for k, v in self.config.items() if k !=
                     'filemanager'}
        return json.dumps({
            'type': self.type,
            'config': sanitized,
            'status': self.status,
            'depends_on': self.depends_on,
            'job_id': self.job_id,
            'year_set': self.year_set
        }, sort_keys=True, indent=4)

    @property
    def type(self):
        return self._type

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, status):
        self._status = status

    def prevalidate(self, config):
        """
        Create input and output directories
        """
        self.config = config
        self.status = JobStatus.VALID
        if not os.path.exists(self.config.get('run_scripts_path')):
            os.makedirs(self.config.get('run_scripts_path'))
        if not os.path.exists(self.config['input_path']):
            msg = 'Creating input directory at {}'.format(
                self.config['input_path'])
            print_line(
                ui=self.config.get('ui', False),
                line=msg,
                event_list=self.event_list,
                current_state=True)
            os.makedirs(self.config['input_path'])

        if self.year_set == 0:
            self.status = JobStatus.INVALID
        else:
            self.status = JobStatus.VALID

    def postvalidate(self):
        """
        Check that what the job was supposed to do actually happened
        returns 1 if the job is done, 0 otherwise
        """
        msg = 'aprime-{}-{}: running postvalidate'.format(self.start_year, self.end_year)
        logging.info(msg)
        # find the directory generated by coupled diags
        if not os.path.exists(self.output_path):
            msg = 'aprime-{}-{}: output directory missing'.format(self.start_year, self.end_year)
            logging.error(msg)
            return False

        try:
            output_contents = os.listdir(self.output_path)
        except IOError:
            msg = 'aprime-{}-{}: failed to read output directory'.format(self.start_year, self.end_year)
            logging.error(msg)
            return False

        if not output_contents:
            msg = 'aprime-{}-{}: output directory empty'.format(self.start_year, self.end_year)
            logging.error(msg)
            return False

        if not self._check_links():
            # links arent in the correct place
            msg = 'aprime-{}-{}: some links are broken in output page'.format(self.start_year, self.end_year)
            logging.error(msg)

            # create host directory and copy in output
            return self._copy_output_to_host_location()

        return True
    
    def _copy_output_to_host_location(self):
        """
        Copies plot output to the correct host location and renders index.html
        """
        output_contents_length = 199
        output_path = os.path.join(
            self.config['output_path'],
            'coupled_diagnostics',
            '{exp}_vs_obs'.format(exp=self.config['experiment']),
            '{exp}_years{start}-{end}_vs_obs'.format(
                exp=self.config['experiment'],
                start=self.start_year,
                end=self.end_year))

        if not os.path.exists(output_path) or not os.path.isdir(output_path):
            return False
        output_contents = os.listdir(output_path)
        if len(output_contents) < output_contents_length:
            return False

        if os.path.exists(self.config['target_host_path']):
            rmtree(self.config['target_host_path'])
        copy2(
            src=output_path,
            dst=self.config['target_host_path'])
        
        msg = 'aprime-{}-{}: native index generation failed, rendering from resource'.format(
            job.start_year, job.end_year)
        logging.info(msg)
        variables = {
            'experiment': self.config['experiment'],
            'start_year': '{:04d}'.format(self.start_year),
            'end_year': '{:04d}'.format(self.end_year)
        }
        resource_path = os.path.join(
            self.config['resource_path'],
            'aprime_index.html')
        output_path = os.path.join(
            self.config['target_host_dir'],
            'index.html')
        try:
            render(
                variables=variables,
                input_path=resource_path,
                output_path=output_path)
        except:
            msg = 'aprime-{}-{}: failed to render from resource'.format(
                job.start_year, job.end_year)
            logging.error(msg)
            return False
        else:
            return True

    def _check_links(self):
        """
        Check that all the links exist in the output page

        returns True if all the links are found, False otherwise
        """
        found = False
        host_directory = "{experiment}_years{start}-{end}_vs_obs".format(
            experiment=self.config['experiment'],
            start=self.start_year,
            end=self.end_year)

        web_dir = os.path.join(
            self.config['web_dir'],
            os.environ['USER'],
            host_directory)

        for path in [web_dir, self.config['target_host_path']]:
            page_path = os.path.join(path, 'index.html')
            page_head, _ = os.path.split(page_path)
            if os.path.exists(page_path):
                found = True
                break
        
        if not found:
            msg = 'aprime-{}-{}: couldnt find output page in search locations: {} or {}'.format(
                self.start_year, self.end_year, self.config['web_dir'], self.config['target_host_path'])
            logging.error(msg)
            return False
        else:
            msg = 'aprime-{}-{}: found output index.html at {}'.format(self.start_year, self.end_year, page_path)
            logging.info(msg)

        missing_pages = list()
        with open(page_path, 'r') as fp:
            page = BeautifulSoup(fp, 'lxml')
            links = page.findAll('a')
            for link in links:
                link_path = os.path.join(page_head, link.attrs['href'])
                if not os.path.exists(link_path):
                    missing_pages.append(link.attrs['href'])

        if missing_pages:
            msg = 'aprime-{start:04d}-{end:04d}: missing plots: {plots}'.format(
                start=self.start_year,
                end=self.end_year,
                plots=missing_pages)
            print_line(
                ui=self.config.get('ui', False),
                line=msg,
                event_list=self.event_list,
                current_state=False)
            return False

        msg = 'All links found for aprime-{start:04d}-{end:04d}'.format(
            start=self.start_year, end=self.end_year)
        print_line(
            ui=self.config.get('ui', False),
            line=msg,
            event_list=self.event_list,
            current_state=True)
        return True

    def ready_check(self):
        missing_files = list()
        monthly_files = 12 * (self.end_year - self.start_year + 1)
        types = ['atm', 'ocn', 'ice', 'streams.ocean',
                 'streams.cice', 'rest', 'mpas-o_in',
                 'mpas-cice_in', 'meridionalHeatTransport',
                 'mpascice.rst']
        for datatype in types:
            new_files = self.filemanager.get_file_paths_by_year(
                start_year=self.start_year,
                end_year=self.end_year,
                _type=datatype)
            if new_files is None or len(new_files) == 0:
                missing_files.append(datatype)
                continue
            if datatype in ['atm', 'ocn', 'ice']:
                if len(new_files) < monthly_files:
                    missing_files.append(datatype)
        if missing_files:
            msg = 'Aprime-{}-{} missing data: {}'.format(
                self.start_year, self.end_year, missing_files)
            logging.info(msg)
            return False
        return True

    def execute(self):
        """
        Setup the run script which will symlink in all the required data,
        and submit that script to resource manager
        """
        if not self.ready_check():
            return -1

        # First check if the job has already been completed
        if self.postvalidate():
            self.status = JobStatus.COMPLETED
            return 0

        # set the job to pending so it doesnt get double started
        self.status = JobStatus.PENDING

        set_string = '{start:04d}_{end:04d}'.format(
            start=self.config.get('start_year'),
            end=self.config.get('end_year'))

        # Setup output directory
        if not os.path.exists(self.config['output_path']):
            msg = 'Aprime-{}-{} setting up output directory'.format(
                self.start_year, self.end_year)
            logging.info(msg)
            os.makedirs(self.config['output_path'])

        # render run template
        run_aprime_template_out = os.path.join(
            self.output_path,
            'run_aprime.bash')
        variables = {
            'www_dir': self.config['web_dir'],
            'output_base_dir': self.output_path,
            'test_casename': self.config['experiment'],
            'test_archive_dir': self.config['input_path'],
            'test_atm_res': self.config['test_atm_res'],
            'test_mpas_mesh_name': self.config['test_mpas_mesh_name'],
            'begin_yr': self.start_year,
            'end_yr': self.end_year
        }
        render(
            variables=variables,
            input_path=self.config['template_path'],
            output_path=run_aprime_template_out)

        # copy the template into the run_scripts directory
        run_name = '{type}_{start:04d}_{end:04d}'.format(
            start=self.start_year,
            end=self.end_year,
            type=self.type)
        template_copy = os.path.join(
            self.config.get('run_scripts_path'),
            run_name)
        copyfile(
            src=run_aprime_template_out,
            dst=template_copy)
        # create the slurm run script
        cmd = 'sh {run_aprime}'.format(
            run_aprime=run_aprime_template_out)

        run_script = os.path.join(
            self.config.get('run_scripts_path'),
            run_name)
        if os.path.exists(run_script):
            os.remove(run_script)

        # render the submission script, which includes input directory setup
        input_files = list()
        types = ['atm', 'ocn', 'ice', 'streams.ocean',
                 'streams.cice', 'rest', 'mpas-o_in',
                 'mpas-cice_in', 'meridionalHeatTransport',
                 'mpascice.rst']
        for datatype in types:
            new_files = self.filemanager.get_file_paths_by_year(
                start_year=self.start_year,
                end_year=self.end_year,
                _type=datatype)
            input_files += new_files
        if self.config['simulation_start_year'] != self.start_year:
            input_files += self.filemanager.get_file_paths_by_year(
                start_year=self.config['simulation_start_year'],
                end_year=self.config['simulation_start_year'] + 1,
                _type='ocn')
        variables = {
            'ACCOUNT': self.config.get('account', ''),
            'WORKDIR': self.config.get('aprime_code_path'),
            'CONSOLE_OUTPUT': '{}.out'.format(run_script),
            'FILES': input_files,
            'INPUT_PATH': self.config['input_path'],
            'EXPERIMENT': self.config['experiment'],
            'SCRIPT_PATH': run_aprime_template_out
        }

        head, _ = os.path.split(self.config['template_path'])
        submission_template_path = os.path.join(
            head, 'aprime_submission_template.sh')
        logging.info('Rendering submision script for aprime')
        # logging.info(json.dumps({'variables': variables, 'input_path': submission_template_path, 'output_path': run_script}))
        render(
            variables=variables,
            input_path=submission_template_path,
            output_path=run_script)

        msg = 'Submitting to queue {type}: {start:04d}-{end:04d}'.format(
            type=self.type,
            start=self.start_year,
            end=self.end_year)
        print_line(
            ui=self.config.get('ui', False),
            line=msg,
            event_list=self.event_list,
            current_state=True)
        slurm = Slurm()
        self.job_id = slurm.batch(run_script)
        status = slurm.showjob(self.job_id)
        self.status = StatusMap[status.get('JobState')]

        return self.job_id
