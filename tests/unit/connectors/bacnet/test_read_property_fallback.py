#     Copyright 2026. ThingsBoard
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
import logging
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from bacpypes3.apdu import AbortPDU, AbortReason, ReadPropertyMultipleRequest, RejectPDU, RejectReason
from bacpypes3.basetypes import ErrorType
from bacpypes3.primitivedata import ObjectIdentifier

from thingsboard_gateway.connectors.bacnet.application import Application


class BacnetReadPropertyFallbackTestCase(IsolatedAsyncioTestCase):
    """
    Some devices (for example MS/TP drives behind a BACnet/IP router) do not implement
    ReadPropertyMultiple and answer it with a Reject "unrecognized-service". The connector
    should then read the same properties with single ReadProperty requests.
    """

    def setUp(self):
        self.application = Application.__new__(Application)
        self.application._Application__log = logging.getLogger('Bacnet test')
        self.device = SimpleNamespace(details=SimpleNamespace(object_id=1003,
                                                              address='6661:3@192.168.168.101',
                                                              vendor_id=15,
                                                              read_property_multiple_supported=True))
        self.object_list = [
            {'objectType': 'analogInput', 'objectId': 0, 'propertyId': 'presentValue'},
            {'objectType': 'analogValue', 'objectId': 3, 'propertyId': {'presentValue', 'units'}},
        ]
        self.values = {
            ('analog-input', 0, 'present-value'): 51.64,
            ('analog-value', 3, 'present-value'): 0.0,
            ('analog-value', 3, 'units'): 'hertz',
        }

        async def read_property(address, objid, prop, array_index=None):
            return self.values[(str(objid[0]), objid[1], str(prop))]

        self.application.read_property = AsyncMock(side_effect=read_property)

    @staticmethod
    def as_dict(result):
        return {(str(obj[0]), obj[1], str(prop)): value for obj, prop, _, value in result}

    async def test_falls_back_to_read_property_on_unrecognized_service(self):
        self.application.request = AsyncMock(side_effect=RejectPDU(reason=RejectReason.unrecognizedService))

        result = await self.application.read_multiple_objects(self.device, self.object_list)

        self.assertEqual(self.as_dict(result), self.values)
        self.assertIsInstance(self.application.request.await_args.args[0], ReadPropertyMultipleRequest)
        self.assertFalse(self.device.details.read_property_multiple_supported)

    async def test_does_not_retry_read_property_multiple_after_fallback(self):
        self.application.request = AsyncMock(side_effect=RejectPDU(reason=RejectReason.unrecognizedService))

        await self.application.read_multiple_objects(self.device, self.object_list)
        result = await self.application.read_multiple_objects(self.device, self.object_list)

        self.assertEqual(self.application.request.await_count, 1)
        self.assertEqual(self.as_dict(result), self.values)

    async def test_other_reject_reasons_do_not_trigger_fallback(self):
        self.application.request = AsyncMock(side_effect=RejectPDU(reason=RejectReason.bufferOverflow))

        result = await self.application.read_multiple_objects(self.device, self.object_list)

        self.assertEqual(result, [])
        self.application.read_property.assert_not_awaited()
        self.assertTrue(self.device.details.read_property_multiple_supported)

    async def test_abort_does_not_trigger_fallback(self):
        self.application.request = AsyncMock(side_effect=AbortPDU(reason=AbortReason.segmentationNotSupported))

        result = await self.application.read_multiple_objects(self.device, self.object_list)

        self.assertEqual(result, [])
        self.application.read_property.assert_not_awaited()
        self.assertTrue(self.device.details.read_property_multiple_supported)

    async def test_error_for_one_property_is_reported_and_others_are_read(self):
        self.device.details.read_property_multiple_supported = False
        self.values[('analog-value', 3, 'units')] = RejectPDU(reason=RejectReason.unrecognizedService)

        result = await self.application.read_multiple_objects(self.device, self.object_list)
        values = self.as_dict(result)

        self.assertEqual(values[('analog-input', 0, 'present-value')], 51.64)
        self.assertEqual(values[('analog-value', 3, 'present-value')], 0.0)
        self.assertIsInstance(values[('analog-value', 3, 'units')], ErrorType)

    async def test_all_properties_are_skipped_in_fallback(self):
        self.device.details.read_property_multiple_supported = False

        result = await self.application.read_multiple_objects(
            self.device, [{'objectId': ObjectIdentifier('analog-input,0'), 'propertyId': 'all'}])

        self.assertEqual(result, [])
        self.application.read_property.assert_not_awaited()
