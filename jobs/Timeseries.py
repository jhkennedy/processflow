# pylint: disable=C0103
# pylint: disable=C0111
# pylint: disable=C0301
import os
import re
import json
import sys
import logging

from uuid import uuid4
from pprint import pformat
from subprocess import Popen, PIPE
from time import sleep

from lib.util import print_debug
from lib.util import print_message
from lib.util import check_slurm_job_submission
from lib.util import push_event
from lib.util import cmd_exists
from lib.util import get_climo_output_files
from JobStatus import JobStatus


class Timeseries(object):
    """
    A wrapper around ncclimo, used to compute the climotologies from raw output data
    """
    def __init__(self, config, event_list):
        self.event_list = event_list
        self.config = {}
        self.status = JobStatus.INVALID
        self.type = 'timeseries'
        self.uuid = uuid4().hex
        self.year_set = config.get('year_set', 0)
        self.job_id = 0
        self.depends_on = []
        self.outputs = {
            'status': self.status,
            'climos': '',
            'regrid': '',
            'console_output': ''
        }
        self.inputs = {
            'annual_mode': '',
            'start_year': '',
            'end_year': '',
            'input_directory': '',
            'output_directory': '',
            'var_list': '',
            'caseId': ''
        }
        self.proc = None
        self.slurm_args = {
            'num_cores': '-n 16', # 16 cores
            'run_time': '-t 0-05:00', # 2 hours run time
            'num_machines': '-N 1', # run on one machine
            'oversubscribe': '--oversubscribe'
        }
        self.prevalidate(config)

    def get_type(self):
        """
        Returns job type
        """
        return self.type

    def execute(self, batch=False):
        """
        Calls ncclimo in a subprocess
        """
        output_dir = os.listdir(self.config['output_directory'])
        var_list_split = self.config['var_list'].split(',')
        complete = True
        for out in output_dir:
            found = False
            for var in var_list_split:
                if var in out:
                    found = True
                    break
            if not found:
                complete = False
        if complete:
            self.status = JobStatus.COMPLETED
            self.event_list = push_event(self.event_list, 'Timeseries already computed, skipping')
            return 0

        input_dir = self.config['input_directory']
        file_list = [os.path.join(input_dir, file) for file in os.listdir(input_dir)]
        cmd = [
            'ncclimo',
            '-a', self.config['annual_mode'],
            '-c', self.config['caseId'],
            '-v', self.config['var_list'],
            '-s', str(self.config['start_year']),
            '-e', str(self.config['end_year']),
            '-o', self.config['output_directory'],
            ' '.join(file_list)
        ]
        if not batch:
            # Not running in batch mode
            while True:
                try:
                    self.proc = Popen(
                        cmd,
                        stdout=PIPE,
                        stderr=PIPE,
                        shell=False)
                    break
                except:
                    sleep(1)
            self.status = JobStatus.RUNNING
            done = 2
            console_output = ''
            while done != 0:
                done = self.proc.poll()
                lines = self.proc.stdout.readlines()
                for line in lines:
                    console_output += line
                lines = self.proc.stderr.readlines()
                for line in lines:
                    console_output += line
                print console_output
                if done < 0:
                    break
                sleep(1)
                self.outputs['console_output'] = console_output
                print console_output

            self.status = JobStatus.COMPLETED
            return 0
        else:
            # Submitting the job to SLURM
            expected_name = 'timeseries_set_{year_set}_{start}_{end}_{uuid}'.format(
                year_set=self.year_set,
                start=self.config.get('start_year'),
                end=self.config.get('end_year'),
                uuid=self.uuid[:5])
            run_scripts_path = os.path.join(os.getcwd(), 'run_scripts')
            run_script = os.path.join(run_scripts_path, expected_name)

            if not os.path.exists(run_scripts_path):
                os.makedirs(run_scripts_path)

            self.slurm_args['error_file'] = '-e {error_file}'.format(error_file=run_script + '.err')
            self.slurm_args['output_file'] = '-o {output_file}'.format(output_file=run_script + '.out')

            with open(run_script, 'w') as batchfile:
                batchfile.write('#!/bin/bash\n')
                slurm_prefix = '\n'.join(['#SBATCH ' + self.slurm_args[s] for s in self.slurm_args]) + '\n'
                batchfile.write(slurm_prefix)
                slurm_command = ' '.join(cmd)
                batchfile.write(slurm_command)

            slurm_cmd = ['sbatch', run_script]
            started = False
            retry_count = 0
            while not started and retry_count < 5:
                while True:
                    try:
                        self.proc = Popen(slurm_cmd, stdout=PIPE, stderr=PIPE)
                        break
                    except:
                        sleep(1)
                _, _ = self.proc.communicate()
                started, job_id = check_slurm_job_submission(expected_name)
                if started:
                    self.status = JobStatus.SUBMITTED
                    self.job_id = job_id
                    message = '{type} id: {id} changed state to {state}'.format(
                        type=self.get_type(),
                        id=self.job_id,
                        state=self.status)
                    logging.info('## ' + message)
                    self.event_list = push_event(self.event_list, message)

                else:
                    logging.warning('Error starting climo job, trying again attempt %s', str(retry_count))
                    retry_count += 1

            if retry_count >= 5:
                self.status = JobStatus.FAILED
                message = '{type} id: {id} changed state to {state}'.format(
                    type=self.get_type(),
                    id=self.job_id,
                    state=self.status)
                logging.info('## ' + message)
                self.event_list = push_event(self.event_list, message)
                self.job_id = 0
            return self.job_id

    def set_status(self, status):
        self.status = status

    def save(self, conf_path):
        """
        Saves job configuration to a json file at conf_path
        """
        try:
            with open(conf_path, 'r') as infile:
                config = json.load(infile)
            with open(conf_path, 'w') as outfile:
                config[self.uuid]['inputs'] = self.config
                config[self.uuid]['outputs'] = self.outputs
                config[self.uuid]['type'] = self.type
                json.dump(config, outfile, indent=4)
        except Exception as e:
            print_message('Error saving configuration file')
            print_debug(e)
            raise

    def __str__(self):
        return pformat({
            'type': self.type,
            'config': self.config,
            'status': self.status,
            'depends_on': self.depends_on,
            'uuid': self.uuid,
            'job_id': self.job_id
        }, indent=4)

    def prevalidate(self, config):
        """
        Prerun validation for inputs
        """
        if self.status == JobStatus.VALID:
            return 0
        for i in config:
            if i not in self.inputs:
                # print_message("Unexpected arguement: {}, {}".format(i, config[i]))
                pass
            else:
                self.config[i] = config.get(i)
        self.status = JobStatus.VALID

        # after checking that the job is valid to run,
        # check if the output already exists and the job actually needs to run
        # if os.path.exists(self.config.get('climo_output_directory')):
        #     set_start_year = self.config.get('start_year')
        #     set_end_year = self.config.get('end_year')
        #     contents = os.listdir(self.config.get('climo_output_directory'))

        #     file_list = get_climo_output_files(
        #         input_path=self.config.get('climo_output_directory'),
        #         set_start_year=self.config.get('start_year'),
        #         set_end_year=self.config.get('end_year'))

        #     if len(file_list) >= 17:
        #         self.status = JobStatus.COMPLETED
        #         # print_message('Ncclimo job already computed, skipping', 'ok')
        #         message = 'Ncclimo job already computed, skipping'
        #         self.event_list = push_event(self.event_list, message)
        #     return 0

    def postvalidate(self):
        """
        Post execution validation
        """
        print "post validation"
