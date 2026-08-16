import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import MonthlyReport from './MonthlyReport'
import Trainees from './Trainees'

export default function ManagerView() {
  return (
    <Tabs defaultValue="calendar">
      <TabsList className="mb-4 w-full">
        <TabsTrigger value="calendar" className="flex-1">Team Schedule</TabsTrigger>
        <TabsTrigger value="availability" className="flex-1">My Availability</TabsTrigger>
        <TabsTrigger value="trainees" className="flex-1">Trainees</TabsTrigger>
        <TabsTrigger value="report" className="flex-1">Monthly Report</TabsTrigger>
      </TabsList>
      <TabsContent value="calendar">
        <ManagerCalendar />
      </TabsContent>
      <TabsContent value="availability">
        <EmployeeView />
      </TabsContent>
      <TabsContent value="trainees">
        <Trainees />
      </TabsContent>
      <TabsContent value="report">
        <MonthlyReport />
      </TabsContent>
    </Tabs>
  )
}
