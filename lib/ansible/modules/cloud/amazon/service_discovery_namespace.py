#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = '''
---
module: service_discovery_namespace
short_description: thin wrap for boto servicediscovery client
description:
    - works with service discovery namespaces
notes:
     - https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/servicediscovery.html
version_added: "2.8"
author:
    - "Tad Merchant (@ezmac)"

requirements: [ json, botocore, boto3 ]
options:
    state:
        description:
          - The desired state of the service
        required: true
        choices: ["present", "absent"]
    name:
        description:
          - The name of the service
        required: true
    type:
        description:
            - Type of namespace
        choices: ["DNS_PRIVATE","DNS_PUBLIC"]
        required: true
    vpc_id:
        description:
             - id of vpc associated with DNS_PRIVATE namespaces
        required: false
    wait:
        type: bool
        description:
             - Wait for creation to complete
             - if wait is false, this returns an operation ID
             - if wait is true, returns the namespace
        default: True
    creator_request_id:
        description:
            A unique string that identifies the request and that allows failed CreateService
            requests to be retried without the risk of executing the operation twice.
            CreatorRequestId can be any unique string, for example, a date/time stamp.
            This field is autopopulated if not provided.
        required: false
    description:
        description: Description for the namespace

extends_documentation_fragment:
    - aws
    - ec2
'''

EXAMPLES = '''
# Note: These examples do not set authentication details, see the AWS Guide for details.
  - service_discovery_namespace:
      name: "my-namespace"
      state: present
      type: "DNS_PRIVATE"
'''

RETURN = '''
namespace:
    description: Details of created namespace.
    returned: when creating a namespace with wait=true (default)
    type: complex
    contains:
        id:
            description: Identifier of service discovery namespace
            returned: always
            type: string
        arn:
            description: arn of namespace
            returned: always
            type: string
        name:
            description: name of service discovery service
            returned: always
            type: string
        type:
            description: The type of the namespace. Valid values are DNS_PUBLIC and DNS_PRIVATE .
            type: string
            returned: always
        description:
            description: Description of namespace
            type: string
            returned: always
        service_count:
            description: The number of services that are associated with the namespace.
            type: integer
            returned: if services are associated
        properties:
            type: complex
            returned: always
            description: A complex type that contains information that's specific to the type of the namespace.
            contains:
                dns_properties:
                    type: complex
                    returned: always
                    description: A complex type that contains the ID for the hosted zone that Route 53 creates
                                 when you create a namespace.
                    contains:
                        hosted_zone_id:
                            type: string
                            returned: always
                            description: The ID for the hosted zone that Route 53 creates when you create a namespace.
        create_date:
            type: datetime
            returned: always
            description: datetime of creation
        creator_request_id:
            type: string
            returned: always
            description:
                A unique string that identifies the request and that allows failed requests to be
                retried without the risk of executing an operation twice.


operation_id:
    description: operation id of the non-waited namespace create request
    type: string
    returned: when creating and wait=false

'''


from ansible.module_utils.aws.core import AnsibleAWSModule
from ansible.module_utils.common.dict_transformations import camel_dict_to_snake_dict
from ansible.module_utils.ec2 import ansible_dict_to_boto3_filter_list
from ansible.module_utils.ec2 import ec2_argument_spec
from ansible.module_utils.ec2 import snake_dict_to_camel_dict, map_complex_type, get_ec2_security_group_ids_from_names
try:
    from botocore import waiter as core_waiter
except ImportError:
    pass

sd_waiter_config = {
    "version": 2,
    "waiters": {
        "NamespaceCreated": {
            "delay": 5,
            "maxAttempts": 10,
            "operation": "GetOperation",
            "acceptors": [
                {
                    "state": "success",
                    "matcher": "path",
                    "argument": "Operation.Status",
                    "expected": "SUCCESS"
                }
            ]
        }
    }
}


class ServiceDiscoveryNamespace:
    """Handles servicediscovery namespaces"""

    def __init__(self, module):
        self.module = module
        self.sd = module.client('servicediscovery')

    def get_operation(self, operation_id):
        operation = self.sd.get_operation(OperationId=operation_id)
        return operation

    def get_namespaces_by_name(self, namespace_name, type):
        namespaces = self.list_namespaces(type)
        if (not namespaces):
            return None
        matching_namespaces = []
        for ns in namespaces['Namespaces']:
            if ns['Name'] == namespace_name:
                matching_namespaces.append(ns)
        return matching_namespaces

    def get_namespace(self, namespace_id):
        namespace = self.sd.get_namespace(Id=namespace_id)['Namespace']
        if not namespace:
            return None
        return self.jsonize(namespace)

    def create_namespace(self, name, type, description, creator_request_id, vpc_id, wait):
        if type == "DNS_PRIVATE":
            namespace = self.create_private_dns_namespace(name,
                                                          description,
                                                          creator_request_id,
                                                          vpc_id,
                                                          wait)
        if type == "DNS_PUBLIC":
            namespace = self.create_public_dns_namespace(name,
                                                         description,
                                                         creator_request_id,
                                                         wait)
        return self.jsonize(namespace)

    def list_namespaces(self, type):
        if type:
            filters = ansible_dict_to_boto3_filter_list({'TYPE': type})
        else:
            filters = []
        return self.sd.list_namespaces(Filters=filters)

    def create_public_dns_namespace(self, name, description, creator_request_id, wait):
        params = dict(Name=name)
        # Creator request id and description are optional; only include if given
        if creator_request_id:
            params['CreatorRequestId'] = creator_request_id
        if description:
            params['description'] = description
        response = self.sd.create_public_dns_namespace(**params)

        operation = response['OperationId']
        if wait:
            waiter_model = core_waiter.WaiterModel(waiter_config=sd_waiter_config).get_waiter("NamespaceCreated")
            waiter = core_waiter.Waiter('namespace_created',
                                        waiter_model,
                                        self.sd.get_operation)

            waiter.wait(OperationId=operation)
            operationResponse = self.sd.get_operation(OperationId=operation)
            namespace_id = operationResponse['Operation']['Targets']["NAMESPACE"]
            created_namespace = self.sd.get_namespace(Id=namespace_id)['Namespace']
            return created_namespace
        else:
            return response

    def create_private_dns_namespace(self, name, description, creator_request_id, vpc_id, wait):
        params = dict(Name=name, Vpc=vpc_id)
        # Creator request id and description are optional; only include if given
        if creator_request_id:
            params['CreatorRequestId'] = creator_request_id
        if description:
            params['description'] = description
        response = self.sd.create_private_dns_namespace(**params)

        operation = response['OperationId']
        if wait:
            waiter_model = core_waiter.WaiterModel(waiter_config=sd_waiter_config).get_waiter("NamespaceCreated")
            waiter = core_waiter.Waiter('namespace_created',
                                        waiter_model,
                                        self.sd.get_operation)
            waiter.wait(OperationId=operation)
            operationResponse = self.sd.get_operation(OperationId=operation)
            namespace_id = operationResponse['Operation']['Targets']["NAMESPACE"]
            created_namespace = self.sd.get_namespace(Id=namespace_id)['Namespace']
            return created_namespace
        else:
            return response

    def delete_namespace(self, id):
        response = self.sd.delete_namespace(Id=id)
        return response

    def jsonize(self, service):
        # some fields are datetime which is not JSON serializable
        # make them strings
        if 'createdAt' in service:
            service['createdAt'] = str(service['createdAt'])
        if 'deployments' in service:
            for d in service['deployments']:
                if 'createdAt' in d:
                    d['createdAt'] = str(d['createdAt'])
                if 'updatedAt' in d:
                    d['updatedAt'] = str(d['updatedAt'])
        if 'events' in service:
            for e in service['events']:
                if 'createdAt' in e:
                    e['createdAt'] = str(e['createdAt'])
        return service

def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        state=dict(required=True, choices=['present', 'absent']),
        type=dict(choices=['DNS_PRIVATE', 'DNS_PUBLIC']),
        name=dict(required=True, type='str'),
        creator_request_id=dict(),
        description=dict(),
        wait=dict(required=False, type='bool', default=True),
        vpc_id=dict()
    ))

    module = AnsibleAWSModule(argument_spec=argument_spec,
                              supports_check_mode=False,
                              required_if=[('state', 'present', ['type']),
                                           ('type', 'private', ['vpc_id'])])

    sd_mgr = ServiceDiscoveryNamespace(module)

    if module.params['state'] == 'present':
        try:
            existing = sd_mgr.get_namespaces_by_name(module.params['name'], module.params['type'])
            if (len(existing) == 1):
                namespace = sd_mgr.get_namespace(existing[0]['Id'])
                module.exit_json(changed=False, namespace=camel_dict_to_snake_dict(namespace))

            if (not existing):
                namespace = sd_mgr.create_namespace(module.params['name'],
                                                    module.params['type'],
                                                    module.params['description'],
                                                    module.params['creator_request_id'],
                                                    module.params['vpc_id'],
                                                    module.params['wait'])
                module.exit_json(changed=True, namespace=camel_dict_to_snake_dict(namespace))

        except Exception as e:
            module.fail_json(msg="Exception '" + module.params['name'] + "': " + str(e))

        module.exit_json(**existing)

    if module.params['state'] == 'absent':
        try:
            existing = sd_mgr.get_namespaces_by_name(module.params['name'], module.params['type'])
            sd_mgr.delete_namespace(existing[0]['Id'])
            module.exit_json(changed=True)
        except Exception as e:
            module.fail_json(msg="Exception '" + module.params['name'] + "': " + str(e))


if __name__ == '__main__':
    main()
