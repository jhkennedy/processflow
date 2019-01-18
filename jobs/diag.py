"""
A child of Job, the Diag class is the parent for all diagnostic jobs
"""
import json
import os

from shutil import rmtree
from distutils.dir_util import copy_tree
from subprocess import call

from lib.jobstatus import JobStatus
from lib.util import print_line
from jobs.job import Job

class Diag(Job):
    def __init__(self, *args, **kwargs):
        super(Diag, self).__init__(*args, **kwargs)
        self._host_url = ''
        self._comparison = kwargs.get('comparison', 'obs')

        # setup the comparison name
        if self._comparison == 'obs':
            self._short_comp_name = 'obs'
        else:
            self._short_comp_name = kwargs['config']['simulations'][self.comparison]['short_name']
    # -----------------------------------------------
    @property
    def comparison(self):
        return self._comparison
    # -----------------------------------------------

    def __str__(self):
        return json.dumps({
            'type': self._job_type,
            'start_year': self._start_year,
            'end_year': self._end_year,
            'data_required': self._data_required,
            'depends_on': self._depends_on,
            'id': self._id,
            'comparison': self._comparison,
            'status': self._status.name,
            'case': self._case
        }, sort_keys=True, indent=4)
    # -----------------------------------------------

    def msg_prefix(self):
        return '{type}-{start:04d}-{end:04d}-{case}-vs-{comp}'.format(
                type=self.job_type,
                start=self.start_year,
                end=self.end_year,
                case=self.short_name,
                comp=self._short_comp_name)
    # -----------------------------------------------

    def setup_hosting(self, always_copy, img_source, host_path, event_list):
        """
        Performs file copys for images into the web hosting directory

        Parameters
        ----------
            always_copy (bool): if previous output exists in the target location, should the new output overwrite
            img_source (str): the path to where the images are coming from
            host_path (str): the path for where the images should be hosted
            event_list (EventList): an eventlist to push user notifications into
        """
        if always_copy:
            if os.path.exists(host_path):
                msg = '{prefix}: Removing previous output from host location'.format(
                    prefix=self.msg_prefix())
                print_line(msg, event_list, newline=False)
                rmtree(host_path)
                msg = '... complete'
                print msg
        if not os.path.exists(host_path):
            msg = '{prefix}: Moving files for web hosting'.format(
                prefix=self.msg_prefix())
            print_line(msg, event_list, newline=False)
            copy_tree(
                src=img_source,
                dst=host_path)
            msg = '... complete'
            print msg
            # fix permissions for apache
            msg = '{prefix}: Fixing permissions'.format(
                prefix=self.msg_prefix())
            print_line(msg, event_list, newline=False)
            call(['chmod', '-R', 'go+rx', host_path])
            tail, _ = os.path.split(host_path)
            for _ in range(2):
                call(['chmod', 'go+rx', tail])
                tail, _ = os.path.split(tail)
            msg = '... complete'
            print msg
        else:
            msg = '{prefix}: Files already present at host location, skipping'.format(
                prefix=self.msg_prefix())
            print_line(msg, event_list)
        
    # -----------------------------------------------

    def get_report_string(self):
        """
        Returns a nice report string of job status information
        """

        # if the job failed or img hosting is turned off, report the status and a path to the jobs console output
        if self.status != JobStatus.COMPLETED or not self._host_url:
            return '{prefix} :: {status} :: {console_path}'.format(
                prefix=self.msg_prefix(),
                status=self.status.name,
                console_path=self._console_output_path)
        # otherwise report the status and give a url to view the output
        else:
            return '{prefix} :: {status} :: {url}'.format(
                prefix=self.msg_prefix(),
                status=self.status.name,
                url=self._host_url)
    # -----------------------------------------------

    def setup_temp_path(self, config, *args, **kwards):
        """
        creates the default temp path for diagnostics
        /project/output/temp/case_short_name/job_type/start_end_vs_comparison
        """
        if self._comparison == 'obs':
            comp = 'obs'
        else:
            comp = config['simulations'][self.comparison]['short_name']
        return os.path.join(
            config['global']['project_path'],
            'output', 'temp', self._short_name, self._job_type,
            '{:04d}_{:04d}_vs_{}'.format(self._start_year, self._end_year, comp))
    # -----------------------------------------------

    def get_run_name(self):
        return '{type}_{start:04d}_{end:04d}_{case}_vs_{comp}'.format(
            type=self.job_type,
            run_type=self._run_type,
            start=self.start_year,
            end=self.end_year,
            case=self.short_name,
            comp=self._short_comp_name)
    # -----------------------------------------------
